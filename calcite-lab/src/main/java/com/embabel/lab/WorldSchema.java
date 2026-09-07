package com.embabel.lab;

import com.fasterxml.jackson.databind.JsonNode;
import org.apache.calcite.schema.Table;
import org.apache.calcite.schema.impl.AbstractSchema;

import java.util.*;

/*
 * A world projected as a SQL schema.
 *
 * TWO KINDS OF TABLE, and the distinction is the one the design note argues is
 * load-bearing:
 *
 *   views  — a saved view is already a relation, so it is already a table.
 *   nodes  — but ONLY labels the graph actually stores. A virtual label has no
 *            extent: there is no set of all Dependency nodes, only
 *            dependencies-of-a-repo, so `SELECT * FROM dependency` is not slow,
 *            it is undefined. We detect this as `sampleCount > 0 && exhaustive`,
 *            which is the closest thing the schema endpoint offers to
 *            "persisted".
 *
 * Views are opt-in by name. Running all 114 would fire live producers and LLM
 * reductions, which is exactly the freshness problem the note describes — a
 * catalog that eagerly probes every asset is itself the API storm.
 */
public final class WorldSchema extends AbstractSchema {

    private final Map<String, Table> tables = new LinkedHashMap<>();
    private final Map<String, Map<String, String>> identityColumns = new LinkedHashMap<>();
    private final Map<String, String> viewSource = new LinkedHashMap<>();
    private final List<String> skippedVirtualLabels = new ArrayList<>();

    public WorldSchema(Appliance appliance, JsonNode views, JsonNode schema,
                       Identity identity, Set<String> wantedViews, int nodeLimit) {

        for (JsonNode v : views) {
            String name = v.path("name").asText();
            if (!wantedViews.contains(name)) continue;
            tables.put(name, new RowsTable(() -> appliance.runView(name, "{}")));
            identityColumns.put(name, identity.identityColumns(v.path("cypher").asText("")));
            viewSource.put(name, v.path("source").asText("(world)"));
        }

        for (JsonNode label : schema.path("labels")) {
            String name = label.path("label").asText();

            /*
             * `anchor` is the extent test, and it is served today. It means the
             * label may open a MATCH pattern bare — a real read, or a virtual
             * population implicitly bound by tenancy. False means it is reachable
             * only by traversal from a bound anchor, which is precisely "this has
             * no extent, so it cannot be a table": Dependency and Vulnerability
             * are false, Policy and WatchedRepo true.
             *
             * An earlier pass of this lab tested `exhaustive` instead, found it
             * true everywhere, and wrongly concluded the distinction was not
             * available. It was; the wrong field was read.
             */
            if (!label.path("anchor").asBoolean(true)) {
                skippedVirtualLabels.add(name);
                continue;
            }
            if (label.path("sampleCount").asInt(0) <= 0) continue;
            List<String> props = new ArrayList<>();
            for (JsonNode p : label.path("properties")) props.add(p.path("name").asText());
            if (props.isEmpty()) continue;

            StringBuilder cypher = new StringBuilder("MATCH (n:`").append(name).append("`) RETURN ");
            for (int i = 0; i < props.size(); i++) {
                if (i > 0) cypher.append(", ");
                cypher.append("n.`").append(props.get(i)).append("` AS `").append(props.get(i)).append('`');
            }
            cypher.append(" LIMIT ").append(nodeLimit);
            String q = cypher.toString();
            tables.put(name, new RowsTable(() -> appliance.execute(q)));
        }
    }

    @Override
    protected Map<String, Table> getTableMap() {
        return tables;
    }

    /**
     * Discover foreign keys by containment, and hand them to Calcite as real
     * referential constraints.
     *
     * WHAT THIS IS STANDING IN FOR: with `identity: true` served by the schema
     * API, a foreign key is a DECLARATION — column X of this view is the identity
     * of label L, so it relates to every other column that is. Without it, the
     * only evidence outside the server is that one column's values happen to be a
     * subset of another's, which is how you end up joining a ticker to a company.
     * The design note forbids exactly this inference for exactly this reason. It
     * is implemented here to prove the wiring reaches Calcite, and to measure what
     * a client is shown once it does — not because containment is sound.
     *
     * Costs a full fetch of every table, which is why it is an explicit call
     * rather than something the schema does while being built.
     */
    public int discoverReferentialConstraints() {
        record Key(String table, String column, int index, Set<Object> values) {}
        List<Key> keys = new ArrayList<>();

        for (Map.Entry<String, Table> e : tables.entrySet()) {
            if (!(e.getValue() instanceof RowsTable t)) continue;
            List<String> cols = t.columns();
            for (int i : t.candidateKeyColumns()) {
                Set<Object> vs = new HashSet<>();
                for (Object[] r : t.materializedRows()) vs.add(r[i]);
                keys.add(new Key(e.getKey(), cols.get(i), i, vs));
            }
        }

        int found = 0;
        for (Map.Entry<String, Table> e : tables.entrySet()) {
            if (!(e.getValue() instanceof RowsTable child)) continue;
            List<String> cols = child.columns();
            List<org.apache.calcite.rel.RelReferentialConstraint> out = new ArrayList<>();

            for (int i = 0; i < cols.size(); i++) {
                Set<Object> mine = new HashSet<>();
                for (Object[] r : child.materializedRows()) if (r[i] != null) mine.add(r[i]);
                if (mine.isEmpty()) continue;

                for (Key k : keys) {
                    if (k.table().equals(e.getKey())) continue;
                    if (!k.column().equals(cols.get(i))) continue;
                    if (!k.values().containsAll(mine)) continue;
                    out.add(org.apache.calcite.rel.RelReferentialConstraintImpl.of(
                            List.of("world", e.getKey()), List.of("world", k.table()),
                            List.of(org.apache.calcite.util.mapping.IntPair.of(i, k.index()))));
                    found++;
                }
            }
            child.setReferentialConstraints(out);
        }
        return found;
    }

    public Map<String, Map<String, String>> identityColumns() {
        return identityColumns;
    }

    public Map<String, String> viewSource() {
        return viewSource;
    }

    public List<String> skippedVirtualLabels() {
        return skippedVirtualLabels;
    }
}

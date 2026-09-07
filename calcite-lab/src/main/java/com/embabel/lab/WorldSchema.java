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
            int count = label.path("sampleCount").asInt(0);
            boolean exhaustive = label.path("exhaustive").asBoolean(false);
            if (count <= 0) {
                if (!exhaustive) skippedVirtualLabels.add(name);
                continue;
            }
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

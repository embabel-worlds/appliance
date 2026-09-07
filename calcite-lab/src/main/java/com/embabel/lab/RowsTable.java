package com.embabel.lab;

import com.fasterxml.jackson.databind.JsonNode;
import org.apache.calcite.DataContext;
import org.apache.calcite.linq4j.Enumerable;
import org.apache.calcite.linq4j.Linq4j;
import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.rel.type.RelDataTypeFactory;
import org.apache.calcite.schema.ScannableTable;
import org.apache.calcite.schema.impl.AbstractTable;
import org.apache.calcite.sql.type.SqlTypeName;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.function.Supplier;

/*
 * One Calcite table over one bundle of JSON rows.
 *
 * Deliberately the dumbest possible implementation: everything is fetched once
 * and held. There is no filter pushdown, no lateral join, no streaming — this
 * lab is asking whether the SHAPE is useful, and pushdown is a separate and
 * later question that the design note already argues should be deferred.
 *
 * Types are inferred from the data rather than declared, because the appliance's
 * schema endpoint reports `any` for every realm-projected property. That is a
 * finding, not a shortcut: a door that must guess its own column types has no
 * contract to offer a client.
 */
public final class RowsTable extends AbstractTable implements ScannableTable {

    private final Supplier<JsonNode> fetch;
    private List<String> columns;
    private List<SqlTypeName> types;
    private List<Object[]> rows;
    private long lastFetchMillis = -1;

    private List<org.apache.calcite.rel.RelReferentialConstraint> constraints = List.of();

    public RowsTable(Supplier<JsonNode> fetch) {
        this.fetch = fetch;
    }

    public void setReferentialConstraints(List<org.apache.calcite.rel.RelReferentialConstraint> c) {
        this.constraints = c;
    }

    public List<Object[]> materializedRows() {
        materialize();
        return rows;
    }

    /**
     * Columns whose values are non-null and distinct across every row fetched.
     *
     * THIS IS A FALLBACK AND SHOULD LOSE TO A DECLARATION. The schema API does not
     * serve the `identity: true` that realm types declare, so the only key
     * evidence available to a door outside the server is the data itself. A column
     * can be accidentally unique in a sample and is then a candidate key that is
     * not a key — which is exactly the confident-nonsense failure the design note
     * warns about, arrived at by a different road. Useful to prove the plumbing,
     * not to trust.
     */
    public List<Integer> candidateKeyColumns() {
        materialize();
        List<Integer> keys = new ArrayList<>();
        for (int i = 0; i < columns.size(); i++) {
            java.util.Set<Object> seen = new java.util.HashSet<>();
            boolean unique = !rows.isEmpty();
            for (Object[] r : rows) {
                if (r[i] == null || !seen.add(r[i])) { unique = false; break; }
            }
            if (unique) keys.add(i);
        }
        return keys;
    }

    @Override
    public org.apache.calcite.schema.Statistic getStatistic() {
        materialize();
        List<org.apache.calcite.util.ImmutableBitSet> keys = new ArrayList<>();
        for (int i : candidateKeyColumns()) keys.add(org.apache.calcite.util.ImmutableBitSet.of(i));
        return org.apache.calcite.schema.Statistics.of((double) rows.size(), keys, constraints, List.of());
    }

    public long lastFetchMillis() {
        return lastFetchMillis;
    }

    public List<String> columns() {
        materialize();
        return columns;
    }

    private synchronized void materialize() {
        if (rows != null) return;
        long t0 = System.currentTimeMillis();
        JsonNode result = fetch.get();
        lastFetchMillis = System.currentTimeMillis() - t0;

        JsonNode rowsNode = result.path("rows");
        LinkedHashSet<String> cols = new LinkedHashSet<>();
        for (JsonNode r : rowsNode) r.fieldNames().forEachRemaining(cols::add);
        columns = new ArrayList<>(cols);

        types = new ArrayList<>();
        for (String c : columns) types.add(inferType(rowsNode, c));

        rows = new ArrayList<>();
        for (JsonNode r : rowsNode) {
            Object[] out = new Object[columns.size()];
            for (int i = 0; i < columns.size(); i++) out[i] = coerce(r.get(columns.get(i)), types.get(i));
            rows.add(out);
        }
    }

    /* First non-null value across the whole batch wins; all-null columns become VARCHAR. */
    private static SqlTypeName inferType(JsonNode rowsNode, String col) {
        for (JsonNode r : rowsNode) {
            JsonNode v = r.get(col);
            if (v == null || v.isNull()) continue;
            if (v.isBoolean()) return SqlTypeName.BOOLEAN;
            if (v.isIntegralNumber()) return SqlTypeName.BIGINT;
            if (v.isNumber()) return SqlTypeName.DOUBLE;
            return SqlTypeName.VARCHAR;
        }
        return SqlTypeName.VARCHAR;
    }

    private static Object coerce(JsonNode v, SqlTypeName t) {
        if (v == null || v.isNull()) return null;
        return switch (t) {
            case BOOLEAN -> v.asBoolean();
            case BIGINT -> v.asLong();
            case DOUBLE -> v.asDouble();
            default -> v.isValueNode() ? v.asText() : v.toString();
        };
    }

    @Override
    public RelDataType getRowType(RelDataTypeFactory typeFactory) {
        materialize();
        RelDataTypeFactory.Builder b = typeFactory.builder();
        for (int i = 0; i < columns.size(); i++) {
            RelDataType t = typeFactory.createSqlType(types.get(i));
            b.add(columns.get(i), typeFactory.createTypeWithNullability(t, true));
        }
        return b.build();
    }

    @Override
    public Enumerable<Object[]> scan(DataContext root) {
        materialize();
        return Linq4j.asEnumerable(rows);
    }
}

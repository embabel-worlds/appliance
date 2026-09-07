package com.embabel.lab;

import com.fasterxml.jackson.databind.JsonNode;
import org.apache.calcite.schema.Schema;
import org.apache.calcite.schema.SchemaFactory;
import org.apache.calcite.schema.SchemaPlus;

import java.util.*;

/*
 * The difference between a lab and a client surface.
 *
 * With this, a world is reachable from ANY Calcite JDBC client — sqlline, a
 * JDBC-speaking IDE, anything with the driver on its classpath — using only a
 * model.json and a connection string. No code. That is the honest test of
 * "useful as a client": not whether our own main method can query it, but
 * whether somebody else's tool can, having been told nothing but a URL.
 *
 * Credentials sit in the model file because they are a throwaway local test
 * appliance's, and a lab that needs setup before it runs is a lab nobody runs.
 */
public final class WorldSchemaFactory implements SchemaFactory {

    @Override
    public Schema create(SchemaPlus parentSchema, String name, Map<String, Object> operand) {
        String base = str(operand, "base", "http://127.0.0.1:11043");
        String user = str(operand, "user", "demo");
        String pass = str(operand, "password", "terminat8r");
        int nodeLimit = operand.get("nodeLimit") instanceof Number n ? n.intValue() : 500;

        Set<String> wanted = new LinkedHashSet<>();
        if (operand.get("views") instanceof List<?> list) {
            for (Object o : list) wanted.add(String.valueOf(o));
        }

        Appliance appliance = new Appliance(base, user, pass);
        JsonNode views = appliance.views();
        JsonNode schema = appliance.schema();

        /* An empty `views` list means every view whose params are all defaulted —
         * the only ones a client may safely enumerate without firing producers
         * it did not ask for. */
        if (wanted.isEmpty()) {
            for (JsonNode v : views) {
                boolean allDefaulted = true;
                for (JsonNode p : v.path("params")) {
                    if (p.path("default").isMissingNode() || p.path("default").isNull()) allDefaulted = false;
                }
                if (allDefaulted && !v.path("params").isEmpty()) continue; // still cheap, but opt in explicitly
                if (v.path("params").isEmpty()) wanted.add(v.path("name").asText());
            }
        }

        return new WorldSchema(appliance, views, schema, new Identity(schema), wanted, nodeLimit);
    }

    private static String str(Map<String, Object> operand, String key, String fallback) {
        Object v = operand.get(key);
        return v == null ? fallback : String.valueOf(v);
    }

}

package com.embabel.lab;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/*
 * Recovering which output columns are node identities.
 *
 * THE POINT OF THIS CLASS IS THAT IT SHOULD NOT NEED TO EXIST. Realm type files
 * declare identity structurally — `metadata: identity: "true"` on a property —
 * but GET /api/v1/admin/kg/schema serves only name/type/sparse/description, so
 * the flag never reaches a client. What survives is prose: an author happened to
 * write "IDENTITY:" or "The identity." into the description. We scrape that here
 * as a STAND-IN for the structured field, purely to find out what a real one
 * would buy. Nothing downstream should be trusted further than that.
 *
 * The second half is the gap the design note already predicted: identity is also
 * lost at the RETURN, because `RETURN d.purl AS package` yields a column that no
 * longer knows it is a Dependency. That half IS recoverable, by reading the
 * variable-to-label bindings out of the MATCH and re-attaching them to aliases.
 */
public final class Identity {

    /* `(w:WatchedRepo)` and `(d :Dependency {...})` alike. */
    private static final Pattern BINDING = Pattern.compile("\\((\\w+)\\s*:\\s*(\\w+)");

    /* `RETURN w.fullName AS repo` — the projection that drops the label. */
    private static final Pattern PROJECTION =
            Pattern.compile("(\\w+)\\.(\\w+)\\s+AS\\s+(\\w+)", Pattern.CASE_INSENSITIVE);

    private final Map<String, Set<String>> identityProps = new HashMap<>();

    public Identity(JsonNode schema) {
        for (JsonNode label : schema.path("labels")) {
            Set<String> ids = new LinkedHashSet<>();
            for (JsonNode p : label.path("properties")) {
                String desc = p.path("description").asText("");
                if (desc.contains("IDENTITY") || desc.matches("(?s).*\\bThe identity\\b.*")) {
                    ids.add(p.path("name").asText());
                }
            }
            if (!ids.isEmpty()) identityProps.put(label.path("label").asText(), ids);
        }
    }

    public int labelsWithIdentity() {
        return identityProps.size();
    }

    /**
     * For one view's Cypher, which output columns are identities, and of what label.
     * Returns alias -> "Label.property".
     */
    public Map<String, String> identityColumns(String cypher) {
        Map<String, String> varToLabel = new HashMap<>();
        Matcher b = BINDING.matcher(cypher);
        while (b.find()) varToLabel.put(b.group(1), b.group(2));

        Map<String, String> out = new LinkedHashMap<>();
        Matcher p = PROJECTION.matcher(cypher);
        while (p.find()) {
            String var = p.group(1), prop = p.group(2), alias = p.group(3);
            String label = varToLabel.get(var);
            if (label == null) continue;
            if (identityProps.getOrDefault(label, Set.of()).contains(prop)) {
                out.put(alias, label + "." + prop);
            }
        }
        return out;
    }
}

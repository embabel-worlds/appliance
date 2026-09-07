package com.embabel.lab;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;

/*
 * The whole client surface this lab needs: list the saved views, read the graph
 * schema, run a view, run ad-hoc Cypher. Basic auth, because that is the only
 * scheme the appliance's OpenAPI declares.
 */
public final class Appliance {

    private final String base;
    private final String authHeader;
    private final HttpClient http;
    private final ObjectMapper mapper = new ObjectMapper();

    public Appliance(String base, String user, String password) {
        this.base = base;
        this.authHeader = "Basic " + Base64.getEncoder()
                .encodeToString((user + ":" + password).getBytes(StandardCharsets.UTF_8));
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
    }

    public JsonNode views() {
        return get("/api/v1/admin/kg/views");
    }

    public JsonNode schema() {
        return get("/api/v1/admin/kg/schema");
    }

    /* A view run takes {"args": {...}}; an empty object means "every param at its default". */
    public JsonNode runView(String name, String argsJson) {
        return post("/api/v1/admin/kg/views/" + name + "/run", "{\"args\":" + argsJson + "}");
    }

    public JsonNode execute(String cypher) {
        try {
            String body = mapper.writeValueAsString(mapper.createObjectNode().put("cypher", cypher));
            return post("/api/v1/admin/kg/execute", body);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private JsonNode get(String path) {
        return send(HttpRequest.newBuilder(URI.create(base + path)).GET());
    }

    private JsonNode post(String path, String body) {
        return send(HttpRequest.newBuilder(URI.create(base + path))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body)));
    }

    private JsonNode send(HttpRequest.Builder b) {
        try {
            HttpRequest req = b.header("Authorization", authHeader)
                    .timeout(Duration.ofSeconds(120)).build();
            HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (res.statusCode() / 100 != 2) {
                throw new RuntimeException("HTTP " + res.statusCode() + " for " + req.uri()
                        + ": " + res.body().substring(0, Math.min(300, res.body().length())));
            }
            return mapper.readTree(res.body());
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }
}

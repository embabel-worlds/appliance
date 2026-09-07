package com.embabel.lab;

import com.fasterxml.jackson.databind.JsonNode;
import org.apache.calcite.jdbc.CalciteConnection;
import org.apache.calcite.schema.SchemaPlus;

import java.sql.*;
import java.util.*;

/*
 * The experiment. Point it at a live appliance, project some views and stored
 * labels as SQL tables, then ask the questions a client would actually ask —
 * above all whether two views JOIN on a shared id and give the right answer.
 *
 * Output is a transcript, not assertions. The deliverable of a lab is what it
 * teaches, so every query prints its plan-visible result and its cost.
 */
public final class Lab {

    /*
     * Credentials come from the environment and have no defaults. This repo is
     * public; a working password committed as a fallback is a working password
     * published, however local the appliance it opens.
     */
    private static final String BASE = env("APPLIANCE_BASE", "http://127.0.0.1:11043");
    private static final String USER = required("APPLIANCE_USER");
    private static final String PASS = required("APPLIANCE_PASS");

    /* Params all defaulted, persisted data, no producers, no LLM calls. */
    private static final Set<String> VIEWS = new LinkedHashSet<>(List.of(
            "policy-premium", "policy-claims", "policy-loss-ratio",
            "claim-amounts", "claim-total-loss", "claim-catastrophe", "coverage-premium"));

    public static void main(String[] args) throws Exception {
        Appliance appliance = new Appliance(BASE, USER, PASS);

        System.out.println("== catalog ==");
        JsonNode views = appliance.views();
        JsonNode schema = appliance.schema();
        Identity identity = new Identity(schema);
        System.out.printf("views in world: %d   labels: %d   labels with a scrapeable identity: %d%n",
                views.size(), schema.path("labels").size(), identity.labelsWithIdentity());

        WorldSchema world = new WorldSchema(appliance, views, schema, identity, VIEWS, 500);

        Properties props = new Properties();
        props.setProperty("caseSensitive", "true");
        try (Connection conn = DriverManager.getConnection("jdbc:calcite:", props)) {
            CalciteConnection calcite = conn.unwrap(CalciteConnection.class);
            SchemaPlus root = calcite.getRootSchema();
            root.add("world", world);
            calcite.setSchema("world");

            System.out.println("\n== tables ==");
            try (ResultSet rs = conn.getMetaData().getTables(null, "world", "%", null)) {
                while (rs.next()) System.out.println("  " + rs.getString("TABLE_NAME"));
            }

            System.out.println("\n== inferred identity columns (from cypher RETURN + schema prose) ==");
            world.identityColumns().forEach((view, cols) ->
                    System.out.printf("  %-22s %-22s %s%n", view, world.viewSource().get(view),
                            cols.isEmpty() ? "(none recovered)" : cols));

            System.out.println("\n== queries ==");
            q(conn, "one view as a table",
                    "SELECT * FROM \"world\".\"policy-premium\" ORDER BY \"premium\" DESC");

            q(conn, "JOIN two views on a shared id",
                    "SELECT p.\"policy_number\", p.\"premium\", c.\"claims\" "
                            + "FROM \"world\".\"policy-premium\" p "
                            + "JOIN \"world\".\"policy-claims\" c ON p.\"policy_number\" = c.\"policy_number\" "
                            + "ORDER BY p.\"premium\" DESC");

            q(conn, "THREE-way join across views",
                    "SELECT p.\"policy_number\", p.\"premium\", c.\"claims\", r.\"loss_ratio\" "
                            + "FROM \"world\".\"policy-premium\" p "
                            + "JOIN \"world\".\"policy-claims\" c ON p.\"policy_number\" = c.\"policy_number\" "
                            + "JOIN \"world\".\"policy-loss-ratio\" r ON r.\"policy_number\" = p.\"policy_number\" "
                            + "ORDER BY r.\"loss_ratio\" DESC");

            q(conn, "aggregate the appliance never computed",
                    "SELECT COUNT(*) AS \"policies\", SUM(\"premium\") AS \"total_premium\", "
                            + "AVG(\"premium\") AS \"avg_premium\" FROM \"world\".\"policy-premium\"");

            q(conn, "join a VIEW to a stored NODE table",
                    "SELECT a.\"claim_number\", a.\"loss_payment\", k.\"catastrophe\" "
                            + "FROM \"world\".\"claim-amounts\" a "
                            + "JOIN \"world\".\"claim-catastrophe\" k ON a.\"claim_number\" = k.\"claim_number\" "
                            + "ORDER BY a.\"loss_payment\" DESC");

            q(conn, "reconciliation: SQL-computed loss ratio vs the view's own",
                    "SELECT r.\"policy_number\", r.\"loss_ratio\" AS \"view_says\", "
                            + "CAST(t.\"loss\" AS DOUBLE) / p.\"premium\" AS \"sql_says\" "
                            + "FROM \"world\".\"policy-loss-ratio\" r "
                            + "JOIN \"world\".\"policy-premium\" p ON p.\"policy_number\" = r.\"policy_number\" "
                            + "JOIN (SELECT \"policy_number\", SUM(\"loss_payment\" + \"loss_reserve\") AS \"loss\" "
                            + "      FROM \"world\".\"claim-amounts\" GROUP BY \"policy_number\") t "
                            + "  ON t.\"policy_number\" = r.\"policy_number\"");

            System.out.println("\n== what a client cannot see ==");
            try (ResultSet rs = conn.getMetaData().getImportedKeys(null, "world", "policy-claims")) {
                int n = 0;
                while (rs.next()) n++;
                System.out.println("  foreign keys advertised by JDBC metadata: " + n);
            } catch (Exception e) {
                System.out.println("  foreign key metadata unavailable: " + e.getClass().getSimpleName());
            }
            System.out.println("  virtual labels skipped (no extent): " + world.skippedVirtualLabels().size());
        }
    }

    private static void q(Connection conn, String title, String sql) {
        System.out.println("\n-- " + title);
        long t0 = System.currentTimeMillis();
        try (Statement st = conn.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            ResultSetMetaData md = rs.getMetaData();
            int n = md.getColumnCount();
            StringBuilder head = new StringBuilder("   ");
            for (int i = 1; i <= n; i++) head.append(String.format("%-24s", md.getColumnLabel(i)));
            System.out.println(head);
            int rows = 0;
            while (rs.next()) {
                if (rows < 8) {
                    StringBuilder line = new StringBuilder("   ");
                    for (int i = 1; i <= n; i++) {
                        Object v = rs.getObject(i);
                        line.append(String.format("%-24s", v == null ? "~" : String.valueOf(v)));
                    }
                    System.out.println(line);
                }
                rows++;
            }
            System.out.printf("   [%d rows, %d ms]%n", rows, System.currentTimeMillis() - t0);
        } catch (SQLException e) {
            System.out.println("   FAILED: " + e.getMessage());
        }
    }

    private static String env(String k, String d) {
        String v = System.getenv(k);
        return v == null || v.isBlank() ? d : v;
    }

    private static String required(String k) {
        String v = System.getenv(k);
        if (v == null || v.isBlank()) {
            throw new IllegalStateException("set " + k + " — see FINDINGS.md for how to run the lab");
        }
        return v;
    }
}

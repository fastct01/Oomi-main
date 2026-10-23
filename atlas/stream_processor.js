// Live outbreak feed: every error any oomi logs, matched against the team's antibodies as it happens.
//
// Needs a dedicated (M10+) cluster: Atlas Stream Processing can't read from Free or Flex clusters.
// Run this in mongosh connected to your Stream Processing workspace (README, "Stream Processing").
// It reads new `events` with kind "error", runs $vectorSearch against `antibodies` (Automated
// Embedding embeds the error text), and writes one document per error to `outbreaks`:
//   { ...the error event, closest: { error_text, fix, author, score } | null, cured: true|false }

const CLUSTER = "oomiCluster"; // the name you gave the cluster in the workspace's Connection Registry
const DB = "oomi";

sp.createStreamProcessor("outbreaks", [
  {
    $source: {
      connectionName: CLUSTER, db: DB, coll: "events",
      config: { pipeline: [{ $match: { operationType: "insert", "fullDocument.kind": "error" } }] },
    },
  },
  { $replaceRoot: { newRoot: "$fullDocument" } },
  {
    $vectorSearch: {
      from: { connectionName: CLUSTER, db: DB, coll: "antibodies" },
      as: "matches",
      index: "antibodies_vector",
      path: "error_text",
      query: { text: "$error_text" },
      model: "voyage-4",
      numCandidates: 50,
      limit: 5,
      filter: { status: { $eq: "approved" } },
      pipeline: [
        { $project: { _id: 1, team: 1, error_text: 1, fix: 1, author: 1, score: { $meta: "vectorSearchScore" } } },
      ],
    },
  },
  // Only antibodies from the same team as the error count.
  { $addFields: { matches: { $filter: { input: "$matches", cond: { $eq: ["$$this.team", "$team"] } } } } },
  {
    $addFields: {
      closest: { $ifNull: [{ $arrayElemAt: ["$matches", 0] }, null] },
      cured: { $gte: [{ $ifNull: [{ $getField: { field: "score", input: { $arrayElemAt: ["$matches", 0] } } }, 0] }, 0.85] },
    },
  },
  { $project: { matches: 0 } },
  {
    $merge: {
      into: { connectionName: CLUSTER, db: DB, coll: "outbreaks" },
      on: "_id", whenMatched: "replace", whenNotMatched: "insert",
    },
  },
], { dlq: { connectionName: CLUSTER, db: DB, coll: "outbreaks_dlq" }, tier: "SP2" });

sp.outbreaks.start();
// Check on it:  sp.outbreaks.stats()     Stop it (stops billing):  sp.outbreaks.stop()

// Generated from schemas/v1 by scripts/gen-schema.ts. Do not edit; run `npm run gen:schema`.
/**
 * batch.json: one batch, its provenance, and the manifest it ran under.
 *
 * A fixture's batch id starts with DEMO-, and only a fixture's. Rules this JSON Schema does not
 * express are enforced when a record is loaded; see crashcourt.spec.records.
 */
export interface BatchRecord {
  batch: string;
  manifest_sha256: string;
  provenance: "run" | "fixture";
  v: 1;
}

export type JsonValue = unknown;

/**
 * One history event. Field order is the order of the JSON line.
 *
 * Some kinds require extra fields (op_id, fault_id, data.to, data.hit_seq, and others; see
 * crashcourt.spec.events). Those rules are enforced when an event is loaded and are not
 * expressed in this JSON Schema.
 */
export interface Event {
  attempt?: number | null;
  batch: string;
  cell: string;
  checkpoint_id?: string | null;
  checkpoint_ns?: string | null;
  data?: {
    [k: string]: JsonValue;
  };
  effect_key?: string | null;
  error?: string | null;
  fault_id?: string | null;
  fp?: string | null;
  kind:
    | "client.invoke"
    | "client.ok"
    | "client.fail"
    | "client.info"
    | "dispatch.send"
    | "dispatch.redeliver"
    | "worker.start"
    | "worker.exit"
    | "node.start"
    | "node.end"
    | "saver.put.call"
    | "saver.put.return"
    | "saver.put.raise"
    | "saver.put_writes.call"
    | "saver.put_writes.return"
    | "saver.put_writes.raise"
    | "saver.get_tuple.call"
    | "saver.get_tuple.return"
    | "saver.get_tuple.raise"
    | "saver.list.call"
    | "saver.list.return"
    | "saver.list.raise"
    | "fp.hit"
    | "fp.release"
    | "fp.action"
    | "effect.request"
    | "effect.response"
    | "sink.append"
    | "fault.inject"
    | "fault.heal";
  logical_op?: string | null;
  mono_ns: number;
  op_id?: string | null;
  parent_checkpoint_id?: string | null;
  pid: number;
  proc: string;
  seed: string;
  seq: number;
  status?: ("ok" | "fail" | "info") | null;
  task_id?: string | null;
  thread_id?: string | null;
  trial: number;
  v: 1;
  wall_ns: number;
}

/**
 * Schema v1. The JSON form is sorted, two-space indented, and ends with a newline.
 */
export interface Manifest {
  clause_registry_sha256: string;
  compose_version: string;
  docker_kernel: string;
  docker_version: string;
  generated_at: string;
  git_dirty: boolean;
  git_sha: string;
  image_ids: {
    [k: string]: string;
  };
  images: {
    [k: string]: string;
  };
  libpq_build_version: string;
  libpq_version: string;
  lockfile_sha256: string;
  machine: string;
  os_release: string;
  os_system: string;
  packages: {
    [k: string]: string;
  };
  postgres: {
    [k: string]: string;
  };
  psycopg_impl: string;
  psycopg_wheel: string;
  python_implementation: string;
  python_version: string;
  runner: string;
  schema_version?: 1;
  toxiproxy_version: string;
}

export type Clause = string | null;
export type Evidence = string[];
export type Invariant = string;
export type Outcome =
  | "PASS"
  | "VIOLATION"
  | "EXPECTED"
  | "GAP"
  | "INVALID"
  | "VIOLATION_CANDIDATE"
  | "NO_ANOMALY_UNSPECIFIED";
export type Reason = string | null;
export type Repro = string | null;
export type Reviewed = string;
export type Reviewer = string;

/**
 * result.json: the outcomes of one trial, one per invariant.
 *
 * A fixture's batch id starts with DEMO-, and only a fixture's. What each outcome needs is
 * listed in crashcourt.spec.records. Rules this JSON Schema does not express are enforced when
 * a record is loaded.
 */
export interface ResultRecord {
  batch: string;
  cell: string;
  manifest_sha256: string;
  provenance: "run" | "fixture";
  /**
   * @minItems 1
   */
  results: [InvariantResult, ...InvariantResult[]];
  trial: number;
  v: 1;
}
/**
 * One invariant's outcome in one trial.
 *
 * Each outcome needs some of clause, evidence, repro, review, and reason; the table is in
 * crashcourt.spec.records. Those rules are enforced when a record is loaded and are not
 * expressed in this JSON Schema.
 */
export interface InvariantResult {
  clause?: Clause;
  evidence?: Evidence;
  invariant: Invariant;
  outcome: Outcome;
  reason?: Reason;
  repro?: Repro;
  review?: Review | null;
}
/**
 * The human review a VIOLATION requires.
 */
export interface Review {
  reviewed: Reviewed;
  reviewer: Reviewer;
}

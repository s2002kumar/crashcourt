// Generated from schemas/v1 by scripts/gen-schema.ts. Do not edit; run `npm run gen:schema`.
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
  compose_version: string;
  docker_version: string;
  generated_at: string;
  git_dirty: boolean;
  git_sha: string;
  images: {
    [k: string]: string;
  };
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
  python_implementation: string;
  python_version: string;
  schema_version?: 1;
  toxiproxy_version: string;
}

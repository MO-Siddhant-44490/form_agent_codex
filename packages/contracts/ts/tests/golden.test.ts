// Cross-language golden serialization: the TypeScript validators must accept
// exactly the messages the Python side round-trips (Module 0 acceptance).
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  BrowserActionSchema,
  DocumentFactSchema,
  EnvelopeSchema,
  PageObservationSchema,
  VerificationResultSchema,
} from "../src/generated/index";

const goldenDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "golden");
const golden = (name: string) =>
  JSON.parse(readFileSync(join(goldenDir, `${name}.json`), "utf8"));

describe("golden messages validate", () => {
  it("document_fact", () => {
    const fact = DocumentFactSchema.parse(golden("document_fact"));
    expect(fact.fact_id).toBe("fact-dob-1");
    expect(fact.source?.parser).toBe("docling");
  });

  it("browser_action", () => {
    const action = BrowserActionSchema.parse(golden("browser_action"));
    expect(action.kind).toBe("SET_TEXT");
    expect(action.idempotency_key).toBe("run-42:page-2:field-dob:1998-04-17");
  });

  it("verification_result", () => {
    const result = VerificationResultSchema.parse(golden("verification_result"));
    expect(result.status).toBe("SUCCESS");
  });

  it("page_observation", () => {
    const obs = PageObservationSchema.parse(golden("page_observation"));
    expect(obs.fields).toHaveLength(1);
    expect(obs.fields?.[0]?.input_type).toBe("date");
  });

  it("envelope", () => {
    const env = EnvelopeSchema.parse(golden("envelope"));
    expect(env.protocol_version).toBe("1.0");
    expect(env.message_type).toBe("verification_result");
  });
});

describe("invalid messages are rejected", () => {
  it("unknown action kind", () => {
    expect(() =>
      BrowserActionSchema.parse({ ...golden("browser_action"), kind: "EXECUTE_JS" }),
    ).toThrow();
  });

  it("missing origin", () => {
    const { origin: _origin, ...rest } = golden("browser_action");
    expect(() => BrowserActionSchema.parse(rest)).toThrow();
  });

  it("missing sequence_number", () => {
    const { sequence_number: _seq, ...rest } = golden("browser_action");
    expect(() => BrowserActionSchema.parse(rest)).toThrow();
  });

  it("unknown extra field", () => {
    expect(() =>
      BrowserActionSchema.parse({ ...golden("browser_action"), javascript: "alert(1)" }),
    ).toThrow();
  });

  it("unknown envelope message type", () => {
    expect(() =>
      EnvelopeSchema.parse({ ...golden("envelope"), message_type: "run_shell_command" }),
    ).toThrow();
  });

  it("credential sensitivity on facts", () => {
    // The credential label exists in the enum for classification but Python
    // refuses to construct such facts; TS consumers must treat any that
    // appear as a protocol violation upstream. Schema-level: value must be a
    // known enum member at minimum.
    expect(() =>
      DocumentFactSchema.parse({ ...golden("document_fact"), sensitivity: "top-secret" }),
    ).toThrow();
  });
});

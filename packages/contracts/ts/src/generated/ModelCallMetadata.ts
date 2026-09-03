// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/model_call_metadata.json
import { z } from "zod"

export const ModelCallMetadataSchema = z.object({ "input_tokens": z.union([z.number().int(), z.null()]).default(null), "latency_ms": z.number().int().gte(0), "model_id": z.string(), "output_tokens": z.union([z.number().int(), z.null()]).default(null), "request_fingerprint": z.string(), "retries": z.number().int().gte(0).default(0), "schema_valid": z.boolean() }).strict().describe("Accounting for one model call (plan.md §11.2): what ran, how long, how\nmuch — never the raw request or response content.")
export type ModelCallMetadata = z.infer<typeof ModelCallMetadataSchema>

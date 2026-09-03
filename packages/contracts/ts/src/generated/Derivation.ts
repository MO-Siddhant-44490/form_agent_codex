// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/derivation.json
import { z } from "zod"

export const DerivationSchema = z.object({ "explanation": z.string(), "operation": z.string(), "source_fact_ids": z.array(z.string()).min(1) }).strict().describe("Provenance for a value that was computed or inferred rather than read\ndirectly from a document (invariant 11): what operation produced it, from\nwhich source facts, and a human-readable explanation.")
export type Derivation = z.infer<typeof DerivationSchema>

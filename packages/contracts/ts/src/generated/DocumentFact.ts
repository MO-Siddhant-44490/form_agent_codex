// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/document_fact.json
import { z } from "zod"

export const DocumentFactSchema = z.object({ "confidence": z.number().gte(0).lte(1), "fact_id": z.string(), "key": z.string(), "sensitivity": z.enum(["public","personal","sensitive","credential"]), "source": z.union([z.object({ "bounding_box": z.union([z.array(z.any()).min(4).max(4), z.null()]).default(null), "document_id": z.string(), "page": z.number().int().gte(1), "parser": z.string(), "raw_text": z.union([z.string(), z.null()]).default(null) }).strict().describe("Document region a fact was extracted from (provenance, invariant 11)."), z.null()]).default(null), "status": z.enum(["extracted","corrected","user_provided","conflicted"]), "value": z.string(), "value_type": z.enum(["string","date","number","boolean","email","phone","address","identifier","enum"]) }).strict()
export type DocumentFact = z.infer<typeof DocumentFactSchema>

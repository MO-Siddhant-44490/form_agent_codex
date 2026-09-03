// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/approval_token.json
import { z } from "zod"

export const ApprovalTokenSchema = z.object({ "expires_at": z.string().datetime({ offset: true }), "issued_at": z.string().datetime({ offset: true }), "origin": z.string(), "run_id": z.string(), "scope": z.literal("submit").default("submit"), "token_id": z.string(), "used": z.boolean().default(false) }).strict().describe("Scoped, single-use, origin-bound, expiring submit approval (invariant 1).")
export type ApprovalToken = z.infer<typeof ApprovalTokenSchema>

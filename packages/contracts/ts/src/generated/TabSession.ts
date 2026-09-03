// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/tab_session.json
import { z } from "zod"

export const TabSessionSchema = z.object({ "attached_at": z.string().datetime({ offset: true }), "last_action_seq": z.number().int().gte(0).default(0), "last_observation_seq": z.number().int().gte(0).default(0), "origin": z.string(), "page_fingerprint": z.union([z.string(), z.null()]).default(null), "run_id": z.string(), "tab_id": z.number().int() }).strict()
export type TabSession = z.infer<typeof TabSessionSchema>

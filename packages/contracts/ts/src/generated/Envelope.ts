// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/envelope.json
import { z } from "zod"

export const EnvelopeSchema = z.object({ "message_type": z.enum(["browser_action","page_observation","action_result","verification_result"]), "payload": z.record(z.string(), z.any()), "protocol_version": z.string(), "run_id": z.string(), "sent_at": z.string().datetime({ offset: true }) }).strict()
export type Envelope = z.infer<typeof EnvelopeSchema>

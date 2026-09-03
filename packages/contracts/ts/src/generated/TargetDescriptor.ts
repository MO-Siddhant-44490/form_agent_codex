// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/target_descriptor.json
import { z } from "zod"

export const TargetDescriptorSchema = z.object({ "accessible_name": z.union([z.string(), z.null()]).default(null), "autocomplete": z.union([z.string(), z.null()]).default(null), "bounding_box": z.union([z.array(z.any()).min(4).max(4), z.null()]).default(null), "field_id": z.string(), "input_type": z.union([z.string(), z.null()]).default(null), "label": z.union([z.string(), z.null()]).default(null), "name_attr": z.union([z.string(), z.null()]).default(null), "placeholder": z.union([z.string(), z.null()]).default(null), "role": z.string() }).strict().describe("Resilient semantic locator for a form control (plan.md §9).")
export type TargetDescriptor = z.infer<typeof TargetDescriptorSchema>

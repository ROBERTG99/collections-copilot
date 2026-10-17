/** Convenience aliases over the generated OpenAPI types (`make gen-types`). */
import type { components } from "@/lib/api/schema";

export type { paths } from "@/lib/api/schema";
type S = components["schemas"];
export type QueueItem = S["QueueItem"];
export type QueuePage = S["QueuePage"];
export type AccountDetail = S["AccountDetail"];
export type CaseDetail = S["CaseDetail"];
export type DraftOut = S["DraftOut"];
export type MessageOut = S["MessageOut"];
export type PromiseOut = S["PromiseOut"];
export type CheckOut = S["CheckOut"];
export type AuditEvent = S["AuditEventOut"];

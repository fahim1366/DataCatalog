import { KeysetPageInfo } from '../../catalog/models/catalog.models';

export interface AuditLogItem {
  id: string;
  actor_id?: string | null;
  actor_username: string;
  ip_address?: string | null;
  user_agent?: string | null;
  action: string;
  entity_type: string;
  entity_id: string;
  diff_jsonb: Record<string, any>;
  created_at: string;
}

export interface AuditLogResponse {
  items: AuditLogItem[];
  page_info: KeysetPageInfo;
}

export interface AuditLogFilterParams {
  entity_type?: string;
  entity_id?: string;
  actor_id?: string;
  action?: string;
  start_time?: string;
  end_time?: string;
  cursor?: string;
  limit?: number;
}


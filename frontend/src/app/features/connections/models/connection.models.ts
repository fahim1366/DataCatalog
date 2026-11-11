export type DatabaseEngine = 'POSTGRESQL' | 'MSSQL' | 'ORACLE' | 'MYSQL' | 'CLICKHOUSE';

export interface SyncSchedule {
  id: string;
  connection_id: string;
  cron_expression: string;
  is_enabled: boolean;
  last_run_at?: string | null;
  last_run_status?: string | null;
  last_run_message?: string | null;
  next_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatabaseConnection {
  id: string;
  name: string;
  engine_type: DatabaseEngine;
  host: string;
  port: number;
  database_name: string;
  default_schema?: string | null;
  owner: string;
  is_active: boolean;
  custom_params?: Record<string, unknown>;
  masked_credentials?: Record<string, string>;
  sync_schedule?: SyncSchedule | null;
  created_at: string;
  updated_at: string;
}

export interface ConnectionCreateRequest {
  name: string;
  engine_type: DatabaseEngine;
  host: string;
  port: number;
  database_name: string;
  default_schema?: string;
  owner?: string;
  credentials: Record<string, unknown>;
  custom_params?: Record<string, unknown>;
  cron_expression?: string;
}

export interface ConnectionTestResult {
  success: boolean;
  latency_ms: number;
  dialect: DatabaseEngine;
  message: string;
  diagnostic_details?: Record<string, unknown> | null;
  tested_at: string;
}


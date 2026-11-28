export interface TableVersionItem {
  id: string;
  table_id: string;
  version_num: number;
  created_by_username?: string | null;
  created_at: string;
}

export interface ColumnSnapshot {
  name: string;
  ordinal_position?: number;
  data_type: string;
  raw_data_type?: string;
  is_nullable?: boolean;
  is_primary_key?: boolean;
  is_foreign_key?: boolean;
  default_value?: string | null;
  classification_level?: string;
  [key: string]: any;
}

export interface ModifiedColumnItem {
  column_name: string;
  v1_attributes: Record<string, any>;
  v2_attributes: Record<string, any>;
}

export interface VersionDiffResponse {
  table_id: string;
  v1_num: number;
  v2_num: number;
  added_columns: ColumnSnapshot[];
  removed_columns: ColumnSnapshot[];
  modified_columns: ModifiedColumnItem[];
}

export type DiffChangeType = 'ADDED' | 'REMOVED' | 'MODIFIED';

export interface DiffSideBySideRow {
  column_name: string;
  change_type: DiffChangeType;
  v1_col: ColumnSnapshot | null;
  v2_col: ColumnSnapshot | null;
  modified_attributes?: {
    attribute: string;
    v1_val: any;
    v2_val: any;
  }[];
}

export interface DatabaseSnapshotPoint {
  timestamp: string;
  label: string;
  source: 'SYNC_JOB' | 'SNAPSHOT' | string;
  sync_job_id?: string | null;
  tables_count: number;
  changes_count: number;
}

export type TableChangeStatus = 'ADDED' | 'DROPPED' | 'MODIFIED';

export interface DatabaseTableDiffItem {
  table_id: string;
  schema_name: string;
  table_name: string;
  change_status: TableChangeStatus;
  v1_version_num?: number | null;
  v2_version_num?: number | null;
  added_columns: ColumnSnapshot[];
  removed_columns: ColumnSnapshot[];
  modified_columns: ModifiedColumnItem[];
}

export interface DatabaseVersionDiffResponse {
  connection_id: string;
  v1_ts: string;
  v2_ts: string;
  added_tables_count: number;
  dropped_tables_count: number;
  modified_tables_count: number;
  unchanged_tables_count: number;
  total_column_changes: number;
  tables: DatabaseTableDiffItem[];
}


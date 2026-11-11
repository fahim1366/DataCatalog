export interface DatabaseCompletenessItem {
  connection_id: string;
  database_name: string;
  table_count: number;
  tables_with_desc: number;
  tables_with_owner: number;
  completeness_pct: number;
}

export interface CompletenessReport {
  overall_completeness_pct: number;
  total_databases: number;
  total_tables: number;
  total_columns: number;
  database_breakdown: DatabaseCompletenessItem[];
}

export interface ClassificationBreakdownItem {
  classification_level: string;
  column_count: number;
  affected_table_count: number;
}

export interface ClassificationReport {
  total_classified_columns: number;
  breakdown: ClassificationBreakdownItem[];
}

export interface ReportRefreshResponse {
  status: string;
  message: string;
  refreshed_views: string[];
}


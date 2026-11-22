export interface KeysetPageInfo {
  has_next_page: boolean;
  next_cursor: string | null;
  limit: number;
}

export interface TagItem {
  id: string;
  name: string;
  color: string;
  description?: string | null;
  assignment_count?: number;
  is_governed?: boolean;
}

export type DescriptionPolicy = 'APPROVED_FIRST' | 'SOURCE_FIRST' | 'BOTH';

export interface SearchHit {
  id: string;
  entity_type: 'TABLE' | 'COLUMN' | string;
  name: string;
  database_name: string;
  schema_name: string;
  table_name?: string | null;
  table_type?: string;
  source_description?: string | null;
  approved_description?: string | null;
  effective_description?: string | null;
  rank?: number;
  classification_level?: string | null;
  status: string;
  row_count_estimate?: number;
  size_bytes?: number;
  tags?: TagItem[];
  description_drift_detected?: boolean;
  description_source?: string | null;
}

export interface SearchResponse {
  items: SearchHit[];
  page_info: KeysetPageInfo;
}

export interface TableItem {
  id: string;
  schema_id: string;
  name: string;
  table_type?: string;
  row_count_estimate?: number;
  size_bytes?: number;
  source_description?: string | null;
  approved_description?: string | null;
  effective_description?: string | null;
  owner?: string | null;
  status: string;
  tags?: TagItem[];
  description_drift_detected?: boolean;
}

export interface TableListResponse {
  items: TableItem[];
  page_info: KeysetPageInfo;
}

export interface ColumnItem {
  id: string;
  table_id: string;
  name: string;
  ordinal_position: number;
  data_type: string;
  raw_data_type: string;
  is_nullable: boolean;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  default_value?: string | null;
  source_description?: string | null;
  approved_description?: string | null;
  effective_description?: string | null;
  ai_suggested_description?: string | null;
  description_source?: string | null;
  curated_by_id?: string | null;
  curated_by_username?: string | null;
  curated_at?: string | null;
  classification_level: string;
  status: string;
  tags?: TagItem[];
  description_drift_detected?: boolean;
}

export interface TableDetail {
  id: string;
  schema_id: string;
  schema_name: string;
  database_name: string;
  name: string;
  table_type: string;
  row_count_estimate: number;
  size_bytes: number;
  source_description?: string | null;
  approved_description?: string | null;
  effective_description?: string | null;
  ai_suggested_description?: string | null;
  description_source?: string | null;
  curated_by_id?: string | null;
  curated_by_username?: string | null;
  curated_at?: string | null;
  owner?: string | null;
  status: string;
  description_drift_detected?: boolean;
  columns: ColumnItem[];
  tags: TagItem[];
  categories?: string[];
  custom_properties?: Record<string, unknown>;
}

export interface DescriptionUpdateRequest {
  approved_description: string;
}

export interface PhysicalTableNode {
  id: string;
  name: string;
  table_type: string;
  row_count_estimate: number;
  column_count: number;
}

export interface PhysicalSchemaNode {
  id: string;
  name: string;
  table_count: number;
  tables: PhysicalTableNode[];
}

export interface PhysicalDatabaseNode {
  id: string;
  name: string;
  database_name: string;
  engine_type: string;
  host?: string | null;
  port?: number | null;
  owner?: string | null;
  is_active?: boolean;
  schemas: PhysicalSchemaNode[];
}

export interface CatalogObjectItem {
  id: string;
  name: string;
  object_type: 'TABLE' | 'VIEW' | 'INDEX' | 'PROCEDURE' | 'FUNCTION' | string;
  database_name?: string | null;
  schema_name?: string | null;
  table_name?: string | null;
  details?: Record<string, any>;
  source_description?: string | null;
  approved_description?: string | null;
  description_source?: string | null;
  owner?: string | null;
  created_at?: string | null;
}

export interface CatalogObjectsResponse {
  counts: {
    tables: number;
    views: number;
    indexes: number;
    procedures: number;
    functions: number;
    [key: string]: number;
  };
  tables: CatalogObjectItem[];
  views: CatalogObjectItem[];
  indexes: CatalogObjectItem[];
  procedures: CatalogObjectItem[];
  functions: CatalogObjectItem[];
}

export interface CategoryTreeNode {
  id: string;
  name: string;
  description?: string | null;
  parent_id?: string | null;
  status?: 'ACTIVE' | 'INACTIVE';
  assigned_tables_count?: number;
  assigned_databases_count?: number;
  children_count?: number;
  tables: {
    id: string;
    name: string;
    schema_name?: string;
    database_name?: string;
    row_count_estimate?: number;
    column_count?: number;
  }[];
  databases?: {
    id: string;
    name: string;
    engine_type?: string;
    database_name?: string;
  }[];
  children: CategoryTreeNode[];
}

export interface ChangeLogItem {
  id: string;
  table_id: string;
  change_type: string;
  column_name?: string | null;
  old_value?: Record<string, unknown> | null;
  new_value?: Record<string, unknown> | null;
  actor_username?: string | null;
  created_at: string;
}

export interface BulkTagTarget {
  entity_type: 'DATABASE' | 'SCHEMA' | 'TABLE' | 'COLUMN';
  entity_id: string;
}

export interface BulkTagRequest {
  tag_ids: string[];
  action: 'ASSIGN' | 'REMOVE';
  targets: BulkTagTarget[];
}

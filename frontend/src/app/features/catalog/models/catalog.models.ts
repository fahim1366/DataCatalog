export interface KeysetPageInfo {
  has_next_page: boolean;
  next_cursor: string | null;
  limit: number;
}

export interface SearchHit {
  id: string;
  entity_type: 'TABLE' | 'COLUMN' | string;
  name: string;
  database_name: string;
  schema_name: string;
  table_name?: string | null;
  effective_description?: string | null;
  rank?: number;
  classification_level?: string | null;
  status: string;
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
  effective_description?: string | null;
  owner?: string | null;
  status: string;
}

export interface TableListResponse {
  items: TableItem[];
  page_info: KeysetPageInfo;
}

export interface TagItem {
  id: string;
  name: string;
  color: string;
  description?: string | null;
  is_governed?: boolean;
}

export interface DescriptionUpdateRequest {
  approved_description: string;
}



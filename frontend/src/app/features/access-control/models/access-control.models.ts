export interface UserItem {
  id: string;
  username: string;
  email: string;
  full_name: string;
  phone?: string;
  status: 'ACTIVE' | 'SUSPENDED' | 'DEACTIVATED';
  is_superadmin: boolean;
  roles: string[];
  role_ids: string[];
  groups: string[];
  group_ids: string[];
  last_login_at?: string;
  created_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface UserCreateDto {
  username: string;
  email: string;
  full_name: string;
  password: string;
  phone?: string;
  is_superadmin?: boolean;
  role_ids?: string[];
  group_ids?: string[];
}

export interface UserUpdateDto {
  full_name?: string;
  email?: string;
  phone?: string;
  status?: 'ACTIVE' | 'SUSPENDED' | 'DEACTIVATED';
  is_superadmin?: boolean;
  role_ids?: string[];
  group_ids?: string[];
}

export interface UserDetailData {
  user: UserItem;
  roles_detail: { id: string; name: string; description?: string; assigned_at?: string }[];
  groups_detail: { id: string; name: string; description?: string; assigned_at?: string }[];
  direct_permissions_count: number;
  activity_count: number;
}

export interface GroupItem {
  id: string;
  name: string;
  description?: string;
  is_active: boolean;
  user_count: number;
  role_count: number;
  roles: string[];
  role_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface GroupCreateDto {
  name: string;
  description?: string;
  is_active?: boolean;
  role_ids?: string[];
  user_ids?: string[];
}

export interface GroupUpdateDto {
  name?: string;
  description?: string;
  is_active?: boolean;
}

export interface GroupMemberItem {
  id: string;
  username: string;
  full_name: string;
  email: string;
  status: string;
  assigned_at?: string;
}

export interface RoleItem {
  id: string;
  name: string;
  description?: string;
  is_system_role: boolean;
  user_count: number;
  created_at: string;
}

export interface RoleCreateDto {
  name: string;
  description?: string;
}

export interface RoleUpdateDto {
  name: string;
  description?: string;
}

export interface RoleUserItem {
  id: string;
  username: string;
  full_name: string;
  email: string;
  assignment_type: 'DIRECT' | 'VIA_GROUP';
  group_name?: string;
}

export interface PermissionDefinition {
  action: string;
  name: string;
  description: string;
}

export interface PermissionModule {
  module_key: string;
  module_name: string;
  description: string;
  permissions: PermissionDefinition[];
}

export interface DirectPermissionRule {
  id: string;
  user_id?: string;
  group_id?: string;
  role_id?: string;
  resource_type: 'GLOBAL' | 'DATABASE' | 'SCHEMA' | 'TABLE' | 'COLUMN' | 'CATEGORY';
  resource_id?: string;
  action: string;
  effect: 'ALLOW' | 'DENY';
  created_at: string;
}

export interface EffectivePermissionRow {
  id: string;
  database_id: string;
  database_name: string;
  schema_id?: string;
  schema_name?: string;
  table_id?: string;
  table_name?: string;
  resource_type: string;
  resource_name: string;
  permission: string;
  result: 'ALLOW' | 'DENY';
  source_type: string;
  source_name: string;
  is_inherited: boolean;
  rule_id?: string;
  explanation: string;
}

export interface UserActivityItem {
  id: string;
  actor_username: string;
  action: string;
  entity_type: string;
  entity_id: string;
  diff_jsonb: Record<string, any>;
  ip_address?: string;
  created_at: string;
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
  schemas: PhysicalSchemaNode[];
}

export interface DatabaseUserItem {
  oid: string;
  username: string;
  can_login: boolean;
  is_superuser: boolean;
  can_create_db: boolean;
  can_create_role: boolean;
  connection_limit: number;
  valid_until: string | null;
  member_of_roles: string[];
  active_sessions: number;
}

export interface DatabaseUserUpdateRequest {
  can_login?: boolean | null;
  connection_limit?: number | null;
  valid_until?: string | null;
}

export interface DatabaseUserActionResponse {
  success: boolean;
  message: string;
}

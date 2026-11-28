import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { DbUserMonitorComponent } from './db-user-monitor.component';
import { ConnectionService } from '../../connections/services/connection.service';
import { DbUserService } from '../services/db-user.service';
import { DatabaseConnection } from '../../connections/models/connection.models';
import { CategoryTreeNode } from '../../catalog/models/catalog.models';
import { DatabaseUserItem } from '../models/db-user.models';
import { RbacService } from '../../../core/auth/rbac.service';

describe('DbUserMonitorComponent (Hierarchical Category Tree & Clarified User Filters)', () => {
  let fixture: ComponentFixture<DbUserMonitorComponent>;
  let component: DbUserMonitorComponent;
  let connectionService: ConnectionService;
  let dbUserService: DbUserService;

  const mockConnections: DatabaseConnection[] = [
    {
      id: 'conn-1',
      name: 'Sales PG Database',
      engine_type: 'POSTGRESQL',
      host: '192.168.1.10',
      port: 5432,
      database_name: 'sales_db',
      owner: 'sales_admin',
      is_active: true,
      category_id: 'cat-1',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
    {
      id: 'conn-2',
      name: 'Finance MSSQL Database',
      engine_type: 'MSSQL',
      host: '192.168.1.20',
      port: 1433,
      database_name: 'finance_db',
      owner: 'finance_admin',
      is_active: true,
      category_id: 'cat-sub-1',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
    {
      id: 'conn-3',
      name: 'Unassigned MySQL DB',
      engine_type: 'MYSQL',
      host: '192.168.1.30',
      port: 3306,
      database_name: 'legacy_mysql',
      owner: 'infra_team',
      is_active: false,
      category_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ];

  const mockCategoryTree: CategoryTreeNode[] = [
    {
      id: 'cat-1',
      name: 'Commercial Division',
      description: 'Commercial operations & CRM',
      databases: [{ id: 'conn-1', name: 'Sales PG Database', engine_type: 'POSTGRESQL' }],
      tables: [],
      children: [
        {
          id: 'cat-sub-1',
          name: 'Accounting & Finance',
          description: 'Accounting ledger databases',
          databases: [{ id: 'conn-2', name: 'Finance MSSQL Database', engine_type: 'MSSQL' }],
          tables: [],
          children: [],
        },
      ],
    },
  ];

  const mockUsers: DatabaseUserItem[] = [
    {
      oid: '1001',
      username: 'app_sales_writer',
      can_login: true,
      is_superuser: false,
      can_create_db: false,
      can_create_role: false,
      member_of_roles: ['writer_role'],
      connection_limit: 50,
      valid_until: 'infinity',
      active_sessions: 3,
    },
    {
      oid: '1002',
      username: 'readonly_analyst',
      can_login: true,
      is_superuser: false,
      can_create_db: false,
      can_create_role: false,
      member_of_roles: ['reader_role'],
      connection_limit: 10,
      valid_until: '2027-01-01',
      active_sessions: 1,
    },
    {
      oid: '1003',
      username: 'etl_role_group',
      can_login: false,
      is_superuser: false,
      can_create_db: false,
      can_create_role: false,
      member_of_roles: [],
      connection_limit: -1,
      valid_until: null,
      active_sessions: 0,
    },
  ];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DbUserMonitorComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: RbacService,
          useValue: {
            hasPermission: () => true,
            currentUser: () => ({ is_superadmin: true }),
          },
        },
      ],
    }).compileComponents();

    connectionService = TestBed.inject(ConnectionService);
    dbUserService = TestBed.inject(DbUserService);

    vi.spyOn(connectionService, 'getCategoryTree').mockReturnValue(of(mockCategoryTree));
    vi.spyOn(connectionService, 'getConnections').mockReturnValue(of(mockConnections));
    vi.spyOn(dbUserService, 'getDatabaseUsers').mockReturnValue(of(mockUsers));

    fixture = TestBed.createComponent(DbUserMonitorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should initialize, load categories and connections, and build hierarchical tree', () => {
    expect(component.categories().length).toBe(1);
    expect(component.connections().length).toBe(3);

    const tree = component.displayTree();
    expect(tree.length).toBe(1);
    expect(tree[0].name).toBe('Commercial Division');
    expect(tree[0].connections.length).toBe(1);
    expect(tree[0].children.length).toBe(1);
    expect(tree[0].children[0].name).toBe('Accounting & Finance');
    expect(tree[0].children[0].connections.length).toBe(1);

    // Auto-selected first PG database
    expect(component.selectedConnection()?.id).toBe('conn-1');
  });

  it('should create an uncategorized branch for databases without category assignment', () => {
    const uncat = component.uncategorizedNode();
    expect(uncat).not.toBeNull();
    expect(uncat?.id).toBe('uncategorized');
    expect(uncat?.name).toBe('پایگاه‌های داده دسته‌بندی‌نشده');
    expect(uncat?.connections.length).toBe(1);
    expect(uncat?.connections[0].id).toBe('conn-3');
  });

  it('should filter tree by unified quickTreeSearch and auto-expand matching category', () => {
    component.quickTreeSearch.set('Finance');
    expect(component.hasActiveTreeFilters()).toBe(true);

    const tree = component.displayTree();
    expect(tree.length).toBe(1);
    // Root has child that matches
    expect(tree[0].children.length).toBe(1);
    expect(tree[0].children[0].connections.length).toBe(1);
    expect(tree[0].children[0].connections[0].name).toBe('Finance MSSQL Database');

    // Auto-expanded
    expect(component.isCategoryExpanded(tree[0])).toBe(true);
    expect(component.isCategoryExpanded(tree[0].children[0])).toBe(true);

    // Uncategorized does not match 'Finance'
    expect(component.uncategorizedNode()).toBeNull();

    // Matches database_name and owner as well
    component.quickTreeSearch.set('sales_db');
    expect(component.displayTree()[0].connections.length).toBe(1);

    component.quickTreeSearch.set('infra_team');
    expect(component.displayTree().length).toBe(0);
    expect(component.uncategorizedNode()?.connections[0].id).toBe('conn-3');
  });

  it('should filter tree by Host via quickTreeSearch and Engine Type', () => {
    component.quickTreeSearch.set('192.168.1.30');
    component.filterEngineType.set('MYSQL');

    const tree = component.displayTree();
    expect(tree.length).toBe(0); // Categorized ones don't match

    const uncat = component.uncategorizedNode();
    expect(uncat).not.toBeNull();
    expect(uncat?.connections.length).toBe(1);
    expect(uncat?.connections[0].engine_type).toBe('MYSQL');
  });

  it('should clear tree filters when clearTreeFilters is invoked', () => {
    component.quickTreeSearch.set('Test');
    component.filterEngineType.set('POSTGRESQL');
    expect(component.hasActiveTreeFilters()).toBe(true);

    component.clearTreeFilters();
    expect(component.hasActiveTreeFilters()).toBe(false);
    expect(component.quickTreeSearch()).toBe('');
    expect(component.filterEngineType()).toBe('');
  });

  it('should filter out non-login roles so only can_login === true users appear in table and stats', () => {
    expect(component.users().length).toBe(3);
    expect(component.loginOnlyUsers().length).toBe(2);
    expect(component.loginOnlyUsers().every((u) => u.can_login)).toBe(true);

    // ALL (login users only)
    component.onUserFilterChange('ALL');
    expect(component.filteredUsers().length).toBe(2);
    expect(component.filteredUsers().some((u) => u.username === 'etl_role_group')).toBe(false);

    // ACTIVE (active_sessions > 0)
    component.onUserFilterChange('ACTIVE');
    expect(component.filteredUsers().length).toBe(2);

    // Stats only reflect login users
    const stats = component.userStats();
    expect(stats.total).toBe(2);
    expect(stats.activeUsers).toBe(2);
    expect(stats.activeSessions).toBe(4);
  });

  it('should paginate login users client-side', () => {
    const manyUsers: DatabaseUserItem[] = Array.from({ length: 15 }, (_, idx) => ({
      oid: String(2000 + idx),
      username: `user_${idx + 1}`,
      can_login: true,
      is_superuser: false,
      can_create_db: false,
      can_create_role: false,
      member_of_roles: [],
      connection_limit: 10,
      valid_until: 'infinity',
      active_sessions: 0,
    }));
    component.users.set(manyUsers);
    component.pageSize.set(10);
    component.currentPage.set(1);

    expect(component.paginatedUsers().length).toBe(10);
    expect(component.paginatedUsers()[0].username).toBe('user_1');

    component.currentPage.set(2);
    expect(component.paginatedUsers().length).toBe(5);
    expect(component.paginatedUsers()[0].username).toBe('user_11');
  });

  it('should auto-select first active connection when first connection is inactive', () => {
    const conns: DatabaseConnection[] = [
      { ...mockConnections[2], id: 'inactive-conn', is_active: false },
      { ...mockConnections[0], id: 'active-conn', is_active: true },
    ];
    vi.spyOn(connectionService, 'getConnections').mockReturnValue(of(conns));
    component.loadData();
    expect(component.selectedConnection()?.id).toBe('active-conn');
  });

  it('should render clean informational state and suppress red error alert when engine is unsupported', () => {
    const errorResponse = {
      status: 400,
      error: {
        detail: "قابلیت پایش کاربران برای موتور پایگاه داده 'CLICKHOUSE' پیاده‌سازی نشده است.",
      },
    };
    vi.spyOn(dbUserService, 'getDatabaseUsers').mockReturnValue(
      throwError(() => errorResponse)
    );

    component.loadUsers('conn-clickhouse');
    fixture.detectChanges();

    expect(component.isUnsupportedEngine()).toBe(true);
    expect(component.unsupportedEngineMessage()).toContain('پیاده‌سازی نشده است');
    expect(component.errorMessage()).toBeNull();

    // Verify DOM contains unsupported container and no error alert
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.unsupported-engine-container')).not.toBeNull();
    expect(compiled.querySelector('.alert-danger')).toBeNull();
  });

  it('should render read-only users table without actions column or mutation modals', () => {
    const compiled = fixture.nativeElement as HTMLElement;
    const headers = Array.from(compiled.querySelectorAll('table.data-table thead th')).map(
      (th) => th.textContent?.trim()
    );
    expect(headers.length).toBe(6);
    expect(headers).not.toContain('عملیات');
    expect(compiled.querySelector('.actions-group')).toBeNull();
    expect(compiled.querySelector('.modal-backdrop')).toBeNull();
  });
});

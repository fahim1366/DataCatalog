import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { forkJoin } from 'rxjs';
import { ConnectionService } from '../../connections/services/connection.service';
import { DatabaseConnection } from '../../connections/models/connection.models';
import { CategoryTreeNode } from '../../catalog/models/catalog.models';
import { DbUserService } from '../services/db-user.service';
import { DatabaseUserItem } from '../models/db-user.models';

export type UserFilterType = 'ALL' | 'LOGIN' | 'ROLES';

export interface DbTreeCategoryNode {
  id: string;
  name: string;
  description?: string | null;
  connections: DatabaseConnection[];
  children: DbTreeCategoryNode[];
  hasMatchingDescendants: boolean;
  totalDatabasesCount: number;
  isUncategorized?: boolean;
}

@Component({
  selector: 'app-db-user-monitor',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './db-user-monitor.component.html',
  styleUrls: ['./db-user-monitor.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DbUserMonitorComponent implements OnInit {
  private readonly connectionService = inject(ConnectionService);
  private readonly dbUserService = inject(DbUserService);
  private readonly route = inject(ActivatedRoute);

  // Notifications
  errorMessage = signal<string | null>(null);

  // Backend state
  categories = signal<CategoryTreeNode[]>([]);
  connections = signal<DatabaseConnection[]>([]);
  isLoadingConnections = signal<boolean>(false);
  selectedConnection = signal<DatabaseConnection | null>(null);

  // Unified quick search + engine filter
  quickTreeSearch = signal<string>('');
  filterEngineType = signal<string>('');

  // Tree expansion state
  expandedCategoryIds = signal<Set<string>>(new Set<string>());

  // Users list state
  users = signal<DatabaseUserItem[]>([]);
  isLoadingUsers = signal<boolean>(false);
  isUnsupportedEngine = signal<boolean>(false);
  unsupportedEngineMessage = signal<string | null>(null);
  userSearchQuery = signal<string>('');
  userTypeFilter = signal<UserFilterType>('ALL');

  // Check whether any tree filter is active
  hasActiveTreeFilters = computed<boolean>(() => {
    return Boolean(this.quickTreeSearch().trim() || this.filterEngineType());
  });

  // Predicate to verify if a connection matches unified quick search and engine filter
  matchesConnection(conn: DatabaseConnection): boolean {
    const q = this.quickTreeSearch().trim().toLowerCase();
    if (q) {
      const matchesText =
        conn.name.toLowerCase().includes(q) ||
        conn.database_name.toLowerCase().includes(q) ||
        conn.host.toLowerCase().includes(q) ||
        (conn.owner ? conn.owner.toLowerCase().includes(q) : false);
      if (!matchesText) {
        return false;
      }
    }

    const engine = this.filterEngineType();
    if (engine && conn.engine_type.toUpperCase() !== engine.toUpperCase()) {
      return false;
    }

    return true;
  }

  // Hierarchical Category -> Subcategories -> Databases Tree
  readonly displayTree = computed<DbTreeCategoryNode[]>(() => {
    const rawCategories = this.categories();
    const allConns = this.connections();
    const activeFilters = this.hasActiveTreeFilters();
    const connMap = new Map<string, DatabaseConnection>(allConns.map((c) => [c.id, c]));

    const buildCategoryNode = (cat: CategoryTreeNode): DbTreeCategoryNode => {
      const assigned: DatabaseConnection[] = [];
      const seenIds = new Set<string>();

      if (cat.databases) {
        for (const dbInfo of cat.databases) {
          const c = connMap.get(dbInfo.id);
          if (c && !seenIds.has(c.id)) {
            seenIds.add(c.id);
            assigned.push(c);
          }
        }
      }

      for (const c of allConns) {
        if (c.category_id === cat.id && !seenIds.has(c.id)) {
          seenIds.add(c.id);
          assigned.push(c);
        }
      }

      const matchingConnections = activeFilters
        ? assigned.filter((c) => this.matchesConnection(c))
        : assigned;

      const matchingChildren: DbTreeCategoryNode[] = [];
      for (const child of cat.children || []) {
        const childNode = buildCategoryNode(child);
        if (!activeFilters || childNode.hasMatchingDescendants || childNode.connections.length > 0) {
          matchingChildren.push(childNode);
        }
      }

      const hasMatch =
        matchingConnections.length > 0 ||
        matchingChildren.some((child) => child.hasMatchingDescendants || child.connections.length > 0);

      const totalDbs =
        assigned.length +
        matchingChildren.reduce((acc, child) => acc + child.totalDatabasesCount, 0);

      return {
        id: cat.id,
        name: cat.name,
        description: cat.description,
        connections: matchingConnections,
        children: matchingChildren,
        hasMatchingDescendants: hasMatch,
        totalDatabasesCount: totalDbs,
      };
    };

    const tree: DbTreeCategoryNode[] = [];
    for (const cat of rawCategories) {
      const node = buildCategoryNode(cat);
      if (!activeFilters || node.hasMatchingDescendants || node.connections.length > 0) {
        tree.push(node);
      }
    }

    return tree;
  });

  // Automatic «پایگاه‌های داده دسته‌بندی‌نشده» branch for uncategorized databases
  readonly uncategorizedNode = computed<DbTreeCategoryNode | null>(() => {
    const rawCategories = this.categories();
    const allConns = this.connections();
    const activeFilters = this.hasActiveTreeFilters();

    const assignedIds = new Set<string>();
    const collectAssignedIds = (nodes: CategoryTreeNode[]) => {
      for (const cat of nodes) {
        if (cat.databases) {
          for (const db of cat.databases) {
            assignedIds.add(db.id);
          }
        }
        if (cat.children) {
          collectAssignedIds(cat.children);
        }
      }
    };
    collectAssignedIds(rawCategories);

    for (const c of allConns) {
      if (c.category_id) {
        assignedIds.add(c.id);
      }
    }

    const uncatConns = allConns.filter((c) => !assignedIds.has(c.id));
    if (uncatConns.length === 0) return null;

    const matchingUncat = activeFilters
      ? uncatConns.filter((c) => this.matchesConnection(c))
      : uncatConns;

    if (activeFilters && matchingUncat.length === 0) {
      return null;
    }

    return {
      id: 'uncategorized',
      name: 'پایگاه‌های داده دسته‌بندی‌نشده',
      description: 'پایگاه‌های داده‌ای که به هیچ دسته‌بندی موضوعی منتسب نشده‌اند',
      connections: matchingUncat,
      children: [],
      hasMatchingDescendants: matchingUncat.length > 0,
      totalDatabasesCount: uncatConns.length,
      isUncategorized: true,
    };
  });

  // Total matching databases count across visible tree
  totalFilteredDatabasesCount = computed<number>(() => {
    const tree = this.displayTree();
    const uncat = this.uncategorizedNode();
    let sum = 0;
    const countConns = (node: DbTreeCategoryNode) => {
      sum += node.connections.length;
      for (const ch of node.children) {
        countConns(ch);
      }
    };
    for (const root of tree) {
      countConns(root);
    }
    if (uncat) {
      sum += uncat.connections.length;
    }
    return sum;
  });

  // Determines whether a category node should be expanded (auto-expanded when searching)
  isCategoryExpanded(node: DbTreeCategoryNode): boolean {
    if (this.hasActiveTreeFilters()) {
      return node.hasMatchingDescendants || node.connections.length > 0;
    }
    return this.expandedCategoryIds().has(node.id);
  }

  // Toggles expand/collapse state for a category node
  toggleCategory(node: DbTreeCategoryNode, event?: Event): void {
    if (event) event.stopPropagation();
    const currentlyExpanded = this.isCategoryExpanded(node);
    const next = new Set(this.expandedCategoryIds());
    if (currentlyExpanded) {
      next.delete(node.id);
    } else {
      next.add(node.id);
    }
    this.expandedCategoryIds.set(next);
  }

  // Resets tree search and engine filters
  clearTreeFilters(): void {
    this.quickTreeSearch.set('');
    this.filterEngineType.set('');
  }

  // Filtered users list with clarified filter pills
  filteredUsers = computed<DatabaseUserItem[]>(() => {
    const query = this.userSearchQuery().trim().toLowerCase();
    const filter = this.userTypeFilter();
    let list = this.users();

    if (filter === 'LOGIN') {
      list = list.filter((u) => u.can_login);
    } else if (filter === 'ROLES') {
      list = list.filter((u) => !u.can_login);
    }

    if (query) {
      list = list.filter(
        (u) =>
          u.username.toLowerCase().includes(query) ||
          u.member_of_roles.some((r) => r.toLowerCase().includes(query))
      );
    }

    return list;
  });

  // Counts for summary pills
  userStats = computed(() => {
    const all = this.users();
    return {
      total: all.length,
      loginUsers: all.filter((u) => u.can_login).length,
      roles: all.filter((u) => !u.can_login).length,
      activeSessions: all.reduce((acc, u) => acc + (u.active_sessions || 0), 0),
    };
  });

  ngOnInit(): void {
    this.loadData();
  }

  loadData(): void {
    this.isLoadingConnections.set(true);
    forkJoin({
      categories: this.connectionService.getCategoryTree(),
      connections: this.connectionService.getConnections(),
    }).subscribe({
      next: ({ categories, connections }) => {
        this.categories.set(categories);
        this.connections.set(connections);
        this.isLoadingConnections.set(false);

        // Auto-expand all top-level categories and uncategorized branch initially
        const initialExpanded = new Set<string>();
        categories.forEach((c) => initialExpanded.add(c.id));
        initialExpanded.add('uncategorized');
        this.expandedCategoryIds.set(initialExpanded);

        // Check if query param specifies connectionId
        const targetId = this.route.snapshot.queryParams['connectionId'];
        if (targetId) {
          const match = connections.find((c) => c.id === targetId);
          if (match) {
            this.selectConnection(match);
            return;
          }
        }

        // Auto-select first active connection or fallback to first
        if (connections.length > 0) {
          const autoConn = connections.find((c) => c.is_active) || connections[0];
          this.selectConnection(autoConn);
        }
      },
      error: (err) => {
        this.isLoadingConnections.set(false);
        this.showError('خطا در دریافت ساختار دسته‌بندی‌ها و پایگاه‌های داده: ' + (err.error?.detail || err.message));
      },
    });
  }

  selectConnection(conn: DatabaseConnection): void {
    this.selectedConnection.set(conn);
    this.loadUsers(conn.id);
  }

  loadUsers(connectionId: string): void {
    this.isLoadingUsers.set(true);
    this.users.set([]);
    this.isUnsupportedEngine.set(false);
    this.unsupportedEngineMessage.set(null);
    this.errorMessage.set(null);

    this.dbUserService.getDatabaseUsers(connectionId).subscribe({
      next: (dbUsers) => {
        this.users.set(dbUsers);
        this.isLoadingUsers.set(false);
      },
      error: (err) => {
        this.isLoadingUsers.set(false);
        const detail: string = err.error?.detail || err.message || '';
        // If database engine does not support live DB-user management, display informational card instead of red error alert
        if (
          err.status === 400 &&
          (detail.includes('پیاده‌سازی نشده') ||
           detail.includes('پیادهسازی نشده') ||
           detail.includes('پیاده سازی نشده'))
        ) {
          this.isUnsupportedEngine.set(true);
          this.unsupportedEngineMessage.set(detail);
        } else {
          this.showError('خطا در دریافت لیست کاربران پایگاه داده: ' + detail);
        }
      },
    });
  }

  refreshCurrentUsers(): void {
    const conn = this.selectedConnection();
    if (conn) {
      this.loadUsers(conn.id);
    }
  }

  formatDate(dateStr: string | null): string {
    if (!dateStr || dateStr.toLowerCase() === 'infinity') {
      return 'نامحدود (دائمی)';
    }
    try {
      const d = new Date(dateStr);
      if (isNaN(d.getTime())) return dateStr;
      return new Intl.DateTimeFormat('fa-IR', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).format(d);
    } catch {
      return dateStr;
    }
  }

  private showError(msg: string): void {
    this.errorMessage.set(msg);
    setTimeout(() => this.errorMessage.set(null), 8000);
  }
}

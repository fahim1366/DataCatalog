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
import { ConnectionService } from './services/connection.service';
import { ConnectionDialogComponent } from './connection-dialog.component';
import { HasPermissionDirective } from '../../core/directives/has-permission.directive';
import {
  ConnectionCreateRequest,
  ConnectionTestResult,
  DatabaseConnection,
} from './models/connection.models';
import { CategoryTreeNode } from '../catalog/models/catalog.models';

export interface CategoryTreeItem {
  node: CategoryTreeNode;
  level: number;
  isLeaf: boolean;
  dbCount: number;
}

export type ConnectionSortKey = 'name' | 'host' | 'database_name' | 'owner';
export type SortDirection = 'asc' | 'desc';

@Component({
  selector: 'app-connection-list',
  standalone: true,
  imports: [CommonModule, FormsModule, ConnectionDialogComponent, HasPermissionDirective],
  templateUrl: './connection-list.component.html',
  styleUrls: ['./connection-list.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionListComponent implements OnInit {
  private readonly connectionService = inject(ConnectionService);

  readonly connections = signal<DatabaseConnection[]>([]);
  readonly categoryTree = signal<CategoryTreeNode[]>([]);
  readonly isLoading = signal<boolean>(false);
  readonly isLoadingTree = signal<boolean>(false);
  readonly showDialog = signal<boolean>(false);
  readonly connectionToEdit = signal<DatabaseConnection | null>(null);
  readonly preselectedCategoryId = signal<string | null>(null);
  readonly testingConnectionId = signal<string | null>(null);
  readonly testResults = signal<Record<string, ConnectionTestResult>>({});
  readonly errorMessage = signal<string | null>(null);

  // Delete confirmation
  readonly connectionToDelete = signal<DatabaseConnection | null>(null);

  // Category Tree Navigation Signals
  readonly selectedCategory = signal<CategoryTreeNode | null>(null);
  readonly expandedCategoryIds = signal<Set<string>>(new Set<string>());
  readonly categorySearch = signal<string>('');

  // Table state: filters, sorting, pagination, expanded rows
  readonly searchTerm = signal<string>('');
  readonly engineFilter = signal<string>('ALL');
  readonly statusFilter = signal<'ALL' | 'ACTIVE' | 'INACTIVE'>('ALL');
  readonly sortKey = signal<ConnectionSortKey | null>(null);
  readonly sortDirection = signal<SortDirection>('asc');
  readonly currentPage = signal<number>(1);
  readonly pageSize = signal<number>(10);
  readonly expandedRowIds = signal<Set<string>>(new Set<string>());
  readonly availablePageSizes: number[] = [10, 20, 50];

  // Map of category_id -> count of databases assigned
  readonly categoryDatabaseCounts = computed<Record<string, number>>(() => {
    const counts: Record<string, number> = {};
    for (const c of this.connections()) {
      if (c.category_id) {
        counts[c.category_id] = (counts[c.category_id] || 0) + 1;
      }
    }
    return counts;
  });

  // Flattened hierarchical tree items with indent levels and leaf detection
  readonly flattenedCategoryTree = computed<CategoryTreeItem[]>(() => {
    const raw = this.categoryTree();
    const expanded = this.expandedCategoryIds();
    const counts = this.categoryDatabaseCounts();
    const term = this.categorySearch().trim().toLowerCase();

    const items: CategoryTreeItem[] = [];

    const traverse = (nodes: CategoryTreeNode[], level: number) => {
      for (const node of nodes) {
        const isLeaf = !node.children || node.children.length === 0;
        const dbCount = counts[node.id] || 0;

        if (term) {
          const matchesSelf = node.name.toLowerCase().includes(term);
          const hasMatchingDescendant = this.hasMatchingDescendant(node, term);
          if (matchesSelf || hasMatchingDescendant) {
            items.push({ node, level, isLeaf, dbCount });
            if (node.children && node.children.length > 0) {
              traverse(node.children, level + 1);
            }
          }
        } else {
          items.push({ node, level, isLeaf, dbCount });
          if (!isLeaf && expanded.has(node.id) && node.children) {
            traverse(node.children, level + 1);
          }
        }
      }
    };

    traverse(raw, 0);
    return items;
  });

  // Helper to collect a category ID and all its descendant category IDs
  private getDescendantCategoryIds(node: CategoryTreeNode): Set<string> {
    const ids = new Set<string>();
    const collect = (curr: CategoryTreeNode) => {
      ids.add(curr.id);
      if (curr.children && curr.children.length > 0) {
        for (const child of curr.children) {
          collect(child);
        }
      }
    };
    collect(node);
    return ids;
  }

  // Connections of the selected category (parent branch with all descendants, or leaf) or all
  readonly filteredConnections = computed<DatabaseConnection[]>(() => {
    const selected = this.selectedCategory();
    const all = this.connections();
    if (!selected) {
      return all;
    }
    const allowedCategoryIds = this.getDescendantCategoryIds(selected);
    return all.filter((c) => !!c.category_id && allowedCategoryIds.has(c.category_id));
  });

  // Engines present in the current scope (for the engine filter dropdown)
  readonly availableEngines = computed<string[]>(() => {
    const set = new Set<string>();
    for (const c of this.filteredConnections()) {
      set.add(c.engine_type);
    }
    return Array.from(set).sort();
  });

  // Search + engine + status filters, then sorting
  readonly tableConnections = computed<DatabaseConnection[]>(() => {
    const term = this.searchTerm().trim().toLowerCase();
    const engine = this.engineFilter();
    const status = this.statusFilter();
    const key = this.sortKey();
    const dir = this.sortDirection();

    let list = this.filteredConnections().filter((c) => {
      if (engine !== 'ALL' && c.engine_type !== engine) return false;
      if (status === 'ACTIVE' && !c.is_active) return false;
      if (status === 'INACTIVE' && c.is_active) return false;
      if (term) {
        const haystack = [c.name, c.host, c.database_name, c.owner, c.category_name, c.default_schema]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });

    if (key) {
      const factor = dir === 'asc' ? 1 : -1;
      list = [...list].sort((a, b) => {
        const av = String(a[key] ?? '');
        const bv = String(b[key] ?? '');
        return av.localeCompare(bv, 'fa') * factor;
      });
    }
    return list;
  });

  readonly totalPages = computed<number>(() =>
    Math.max(1, Math.ceil(this.tableConnections().length / this.pageSize()))
  );

  // Current page clamped to a valid range (e.g. after deleting the last row of a page)
  readonly safePage = computed<number>(() => Math.min(this.currentPage(), this.totalPages()));

  readonly paginatedConnections = computed<DatabaseConnection[]>(() => {
    const start = (this.safePage() - 1) * this.pageSize();
    return this.tableConnections().slice(start, start + this.pageSize());
  });

  readonly pageDisplayStart = computed<number>(() =>
    this.tableConnections().length === 0 ? 0 : (this.safePage() - 1) * this.pageSize() + 1
  );

  readonly pageDisplayEnd = computed<number>(() =>
    Math.min(this.safePage() * this.pageSize(), this.tableConnections().length)
  );

  ngOnInit(): void {
    this.loadConnections();
    this.loadCategoryTree();
  }

  loadConnections(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.connectionService.getConnections().subscribe({
      next: (items) => {
        this.connections.set(items);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در بارگذاری لیست اتصالات پایگاه داده';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  loadCategoryTree(): void {
    this.isLoadingTree.set(true);
    this.connectionService.getCategoryTree().subscribe({
      next: (tree) => {
        this.categoryTree.set(tree);
        this.isLoadingTree.set(false);

        // Expand root categories by default
        const initialExpanded = new Set<string>();
        tree.forEach((t) => initialExpanded.add(t.id));
        this.expandedCategoryIds.set(initialExpanded);
      },
      error: () => {
        this.isLoadingTree.set(false);
      },
    });
  }

  private hasMatchingDescendant(node: CategoryTreeNode, term: string): boolean {
    if (!node.children || node.children.length === 0) return false;
    for (const child of node.children) {
      if (child.name.toLowerCase().includes(term) || this.hasMatchingDescendant(child, term)) {
        return true;
      }
    }
    return false;
  }

  // ───── Category tree ─────
  onCategorySearchInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.categorySearch.set(input.value);
  }

  toggleExpandCategory(categoryId: string): void {
    this.expandedCategoryIds.update((prev) => {
      const next = new Set(prev);
      if (next.has(categoryId)) {
        next.delete(categoryId);
      } else {
        next.add(categoryId);
      }
      return next;
    });
  }

  selectCategory(node: CategoryTreeNode | null): void {
    if (!node) {
      this.selectedCategory.set(null);
      this.resetTableView();
      return;
    }
    this.selectedCategory.set(node);
    this.resetTableView();
  }

  // Reset engine filter (it may not exist in the new scope), page and open rows
  private resetTableView(): void {
    this.engineFilter.set('ALL');
    this.currentPage.set(1);
    this.expandedRowIds.set(new Set<string>());
  }

  // ───── Table: filters, sort, pagination, rows ─────
  onFilterChanged(): void {
    this.currentPage.set(1);
  }

  toggleSort(key: ConnectionSortKey): void {
    if (this.sortKey() === key) {
      if (this.sortDirection() === 'asc') {
        this.sortDirection.set('desc');
      } else {
        // Third click clears the sort
        this.sortKey.set(null);
        this.sortDirection.set('asc');
      }
    } else {
      this.sortKey.set(key);
      this.sortDirection.set('asc');
    }
  }

  sortIcon(key: ConnectionSortKey): string {
    if (this.sortKey() !== key) return 'pi-sort-alt';
    return this.sortDirection() === 'asc' ? 'pi-sort-amount-up-alt' : 'pi-sort-amount-down';
  }

  goToPage(page: number): void {
    const target = Math.min(Math.max(1, page), this.totalPages());
    this.currentPage.set(target);
  }

  changePageSize(size: number | string): void {
    this.pageSize.set(Number(size));
    this.currentPage.set(1);
  }

  toggleRow(id: string): void {
    this.expandedRowIds.update((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  // ───── Dialog ─────
  openCreateDialog(categoryId?: string): void {
    this.connectionToEdit.set(null);
    this.preselectedCategoryId.set(categoryId || this.selectedCategory()?.id || null);
    this.showDialog.set(true);
  }

  openEditDialog(conn: DatabaseConnection): void {
    this.connectionToEdit.set(conn);
    this.preselectedCategoryId.set(null);
    this.showDialog.set(true);
  }

  closeDialog(): void {
    this.showDialog.set(false);
    this.connectionToEdit.set(null);
    this.preselectedCategoryId.set(null);
  }

  onSaveConnection(request: ConnectionCreateRequest): void {
    const editConn = this.connectionToEdit();
    if (editConn) {
      this.connectionService.updateConnection(editConn.id, request).subscribe({
        next: (updatedConn) => {
          this.connections.update((list) =>
            list.map((c) => (c.id === updatedConn.id ? updatedConn : c))
          );
          this.closeDialog();
          this.loadCategoryTree();
        },
        error: (err: unknown) => {
          const msg = err instanceof Error ? err.message : 'خطا در بروزرسانی اتصال دیتابیس';
          this.errorMessage.set(msg);
        },
      });
    } else {
      this.connectionService.createConnection(request).subscribe({
        next: (newConn) => {
          this.connections.update((list) => [newConn, ...list]);
          this.closeDialog();
          this.loadCategoryTree();
        },
        error: (err: unknown) => {
          const msg = err instanceof Error ? err.message : 'خطا در ثبت اتصال جدید پایگاه داده';
          this.errorMessage.set(msg);
        },
      });
    }
  }

  // ───── Delete (in-app confirmation instead of window.confirm) ─────
  requestDelete(conn: DatabaseConnection): void {
    this.connectionToDelete.set(conn);
  }

  cancelDelete(): void {
    this.connectionToDelete.set(null);
  }

  confirmDelete(): void {
    const target = this.connectionToDelete();
    if (!target) return;

    this.connectionService.deleteConnection(target.id).subscribe({
      next: () => {
        this.connections.update((list) => list.filter((c) => c.id !== target.id));
        this.connectionToDelete.set(null);
        this.loadCategoryTree();
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در حذف اتصال دیتابیس';
        this.errorMessage.set(msg);
        this.connectionToDelete.set(null);
      },
    });
  }

  // ───── Test connection ─────
  onTestConnection(id: string): void {
    this.testingConnectionId.set(id);

    this.connectionService.testConnection(id).subscribe({
      next: (result) => {
        this.testResults.update((prev) => ({
          ...prev,
          [id]: result,
        }));
        this.testingConnectionId.set(null);
      },
      error: (err: unknown) => {
        const errorResult: ConnectionTestResult = {
          success: false,
          latency_ms: 5000.0,
          dialect: 'POSTGRESQL',
          message: err instanceof Error ? err.message : 'Connection test failed',
          tested_at: new Date().toISOString(),
        };
        this.testResults.update((prev) => ({
          ...prev,
          [id]: errorResult,
        }));
        this.testingConnectionId.set(null);
      },
    });
  }
}

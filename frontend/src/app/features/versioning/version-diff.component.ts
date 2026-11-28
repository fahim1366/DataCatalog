import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { forkJoin, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { VersionService } from './services/version.service';
import {
  ColumnSnapshot,
  DatabaseSnapshotPoint,
  DatabaseTableDiffItem,
  DatabaseVersionDiffResponse,
  DiffSideBySideRow,
  ModifiedColumnItem,
  TableVersionItem,
  VersionDiffResponse,
} from './models/version.models';
import { CategoryTreeNode } from '../catalog/models/catalog.models';
import { DatabaseConnection } from '../connections/models/connection.models';

export interface VersionTreeCategoryNode {
  id: string;
  name: string;
  description?: string | null;
  connections: DatabaseConnection[];
  children: VersionTreeCategoryNode[];
  hasMatchingDescendants: boolean;
  totalDatabasesCount: number;
}

@Component({
  selector: 'app-version-diff',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './version-diff.component.html',
  styleUrls: ['./version-diff.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class VersionDiffComponent implements OnInit {
  private readonly versionService = inject(VersionService);

  // Inputs using Angular Signals
  readonly diffData = input<VersionDiffResponse | null>(null);
  readonly tableId = input<string | null>(null);
  readonly v1 = input<number | null>(null);
  readonly v2 = input<number | null>(null);
  readonly availableVersions = input<TableVersionItem[]>([]);
  readonly syncJobId = input<string | null>(null);

  // Toolbar & Table Selection State (Legacy table-level mode)
  readonly availableTables = signal<Array<{ id: string; name: string; schema_name?: string }>>([]);
  readonly selectedTableId = signal<string>('');
  readonly tableVersions = signal<TableVersionItem[]>([]);
  readonly selectedV1 = signal<number>(1);
  readonly selectedV2 = signal<number>(2);

  // Database-Centric Mode State
  readonly isEmbeddedTableMode = computed<boolean>(() => {
    return !!(this.diffData() || (this.tableId() && this.v1() !== null && this.v2() !== null));
  });
  readonly connections = signal<DatabaseConnection[]>([]);
  readonly rawCategories = signal<CategoryTreeNode[]>([]);
  readonly isLoadingTree = signal<boolean>(false);
  readonly quickTreeSearch = signal<string>('');
  readonly hostTreeSearch = signal<string>('');
  readonly filterEngineType = signal<string>('');
  readonly expandedCategories = signal<Set<string>>(new Set());

  readonly selectedConnection = signal<DatabaseConnection | null>(null);
  readonly snapshotPoints = signal<DatabaseSnapshotPoint[]>([]);
  readonly selectedV1Ts = signal<string>('');
  readonly selectedV2Ts = signal<string>('');
  readonly tableFilterTerm = signal<string>('');
  readonly dbDiff = signal<DatabaseVersionDiffResponse | null>(null);
  readonly isLoadingDbSnapshots = signal<boolean>(false);
  readonly isLoadingDbDiff = signal<boolean>(false);
  readonly dbErrorMessage = signal<string | null>(null);
  readonly expandedTableIds = signal<Set<string>>(new Set());

  readonly hasActiveTreeFilters = computed<boolean>(() => {
    return !!(
      this.quickTreeSearch().trim() ||
      this.hostTreeSearch().trim() ||
      this.filterEngineType()
    );
  });

  private matchesConnection(conn: DatabaseConnection): boolean {
    const q = this.quickTreeSearch().trim().toLowerCase();
    if (q) {
      const nameMatch = conn.name.toLowerCase().includes(q);
      const dbMatch = (conn.database_name || '').toLowerCase().includes(q);
      if (!nameMatch && !dbMatch) return false;
    }

    const hostQ = this.hostTreeSearch().trim().toLowerCase();
    if (hostQ) {
      const hostMatch = (conn.host || '').toLowerCase().includes(hostQ);
      if (!hostMatch) return false;
    }

    const engine = this.filterEngineType();
    if (engine && conn.engine_type.toUpperCase() !== engine.toUpperCase()) {
      return false;
    }

    return true;
  }

  readonly displayTree = computed<VersionTreeCategoryNode[]>(() => {
    const rawCategories = this.rawCategories();
    const allConns = this.connections();
    const activeFilters = this.hasActiveTreeFilters();
    const connMap = new Map<string, DatabaseConnection>(allConns.map((c) => [c.id, c]));

    const buildCategoryNode = (cat: CategoryTreeNode): VersionTreeCategoryNode => {
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

      const matchingChildren: VersionTreeCategoryNode[] = [];
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

    const tree: VersionTreeCategoryNode[] = [];
    for (const cat of rawCategories) {
      const node = buildCategoryNode(cat);
      if (!activeFilters || node.hasMatchingDescendants || node.connections.length > 0) {
        tree.push(node);
      }
    }

    return tree;
  });

  readonly uncategorizedNode = computed<VersionTreeCategoryNode | null>(() => {
    const rawCategories = this.rawCategories();
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

    if (activeFilters && matchingUncat.length === 0) return null;

    return {
      id: '__uncategorized__',
      name: 'پایگاه‌های داده دسته‌بندی‌نشده',
      description: null,
      connections: matchingUncat,
      children: [],
      hasMatchingDescendants: matchingUncat.length > 0,
      totalDatabasesCount: uncatConns.length,
    };
  });

  readonly totalFilteredDatabasesCount = computed<number>(() => {
    const allConns = this.connections();
    if (!this.hasActiveTreeFilters()) return allConns.length;
    return allConns.filter((c) => this.matchesConnection(c)).length;
  });

  readonly filteredDbDiffTables = computed<DatabaseTableDiffItem[]>(() => {
    const diff = this.dbDiff();
    if (!diff) return [];
    const term = this.tableFilterTerm().trim().toLowerCase();
    if (!term) return diff.tables;
    return diff.tables.filter(
      (t) =>
        t.table_name.toLowerCase().includes(term) ||
        t.schema_name.toLowerCase().includes(term)
    );
  });

  // State Signals
  readonly fetchedDiff = signal<VersionDiffResponse | null>(null);
  readonly isLoading = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);
  readonly activeView = signal<'side-by-side' | 'categorized'>('side-by-side');

  // Computed Diff Resolution
  readonly activeDiff = computed<VersionDiffResponse | null>(() => {
    return this.diffData() ?? this.fetchedDiff();
  });

  readonly addedColumns = computed<ColumnSnapshot[]>(() => {
    return this.activeDiff()?.added_columns ?? [];
  });

  readonly removedColumns = computed<ColumnSnapshot[]>(() => {
    return this.activeDiff()?.removed_columns ?? [];
  });

  readonly modifiedColumns = computed<ModifiedColumnItem[]>(() => {
    return this.activeDiff()?.modified_columns ?? [];
  });

  readonly totalChanges = computed<number>(() => {
    return (
      this.addedColumns().length +
      this.removedColumns().length +
      this.modifiedColumns().length
    );
  });

  readonly isEmpty = computed<boolean>(() => {
    const diff = this.activeDiff();
    return !!diff && this.totalChanges() === 0;
  });

  readonly sideBySideRows = computed<DiffSideBySideRow[]>(() => {
    const diff = this.activeDiff();
    if (!diff) return [];

    const rows: DiffSideBySideRow[] = [];

    // 1. Removed columns (present in v1, absent in v2)
    for (const col of diff.removed_columns) {
      rows.push({
        column_name: col.name,
        change_type: 'REMOVED',
        v1_col: col,
        v2_col: null,
      });
    }

    // 2. Modified columns (present in both with altered attributes)
    for (const mod of diff.modified_columns) {
      const v1Col: ColumnSnapshot = {
        name: mod.column_name,
        data_type: mod.v1_attributes?.['data_type'] ?? '—',
        ...mod.v1_attributes,
      };
      const v2Col: ColumnSnapshot = {
        name: mod.column_name,
        data_type: mod.v2_attributes?.['data_type'] ?? '—',
        ...mod.v2_attributes,
      };

      const diffAttrs = Object.keys(mod.v2_attributes || {}).map((key) => ({
        attribute: key,
        v1_val: mod.v1_attributes[key],
        v2_val: mod.v2_attributes[key],
      }));

      rows.push({
        column_name: mod.column_name,
        change_type: 'MODIFIED',
        v1_col: v1Col,
        v2_col: v2Col,
        modified_attributes: diffAttrs,
      });
    }

    // 3. Added columns (present in v2, absent in v1)
    for (const col of diff.added_columns) {
      rows.push({
        column_name: col.name,
        change_type: 'ADDED',
        v1_col: null,
        v2_col: col,
      });
    }

    return rows;
  });

  constructor() {
    effect(() => {
      const tid = this.tableId();
      const ver1 = this.v1();
      const ver2 = this.v2();
      const direct = this.diffData();

      if (!direct && tid && ver1 !== null && ver2 !== null) {
        this.loadDiff(tid, ver1, ver2);
      }
    });
  }

  ngOnInit(): void {
    const tid = this.tableId();
    const ver1 = this.v1();
    const ver2 = this.v2();

    if (!this.diffData() && tid && ver1 !== null && ver2 !== null) {
      this.selectedTableId.set(tid);
      this.selectedV1.set(ver1);
      this.selectedV2.set(ver2);
      this.loadDiff(tid, ver1, ver2);
    } else if (!this.diffData()) {
      this.loadDatabaseTree();
    }
  }

  loadDatabaseTree(): void {
    this.isLoadingTree.set(true);
    forkJoin({
      connections: this.versionService.getConnections().pipe(catchError(() => of([]))),
      categories: this.versionService.getCategoryTree().pipe(catchError(() => of([]))),
    }).subscribe({
      next: ({ connections, categories }) => {
        this.connections.set(connections);
        this.rawCategories.set(categories);
        const expanded = new Set<string>();
        for (const cat of categories) {
          expanded.add(cat.id);
        }
        expanded.add('__uncategorized__');
        this.expandedCategories.set(expanded);
        this.isLoadingTree.set(false);

        if (connections.length > 0 && !this.selectedConnection()) {
          this.selectConnection(connections[0]);
        }
      },
      error: () => {
        this.isLoadingTree.set(false);
      },
    });
  }

  clearTreeFilters(): void {
    this.quickTreeSearch.set('');
    this.hostTreeSearch.set('');
    this.filterEngineType.set('');
  }

  toggleCategory(categoryId: string): void {
    const next = new Set(this.expandedCategories());
    if (next.has(categoryId)) {
      next.delete(categoryId);
    } else {
      next.add(categoryId);
    }
    this.expandedCategories.set(next);
  }

  isCategoryExpanded(categoryId: string): boolean {
    if (this.hasActiveTreeFilters()) return true;
    return this.expandedCategories().has(categoryId);
  }

  selectConnection(conn: DatabaseConnection): void {
    this.selectedConnection.set(conn);
    this.dbDiff.set(null);
    this.dbErrorMessage.set(null);
    this.expandedTableIds.set(new Set());
    this.loadConnectionSnapshots(conn.id);
  }

  loadConnectionSnapshots(connectionId: string): void {
    this.isLoadingDbSnapshots.set(true);
    this.snapshotPoints.set([]);
    this.versionService.getConnectionVersions(connectionId).subscribe({
      next: (points) => {
        this.snapshotPoints.set(points);
        this.isLoadingDbSnapshots.set(false);
        if (points.length >= 2) {
          const v1 = points[0].timestamp;
          const v2 = points[points.length - 1].timestamp;
          this.selectedV1Ts.set(v1);
          this.selectedV2Ts.set(v2);
          this.triggerDatabaseComparison();
        } else if (points.length === 1) {
          const v1 = points[0].timestamp;
          this.selectedV1Ts.set(v1);
          this.selectedV2Ts.set(v1);
          this.triggerDatabaseComparison();
        } else {
          this.selectedV1Ts.set('');
          this.selectedV2Ts.set('');
        }
      },
      error: (err: unknown) => {
        this.isLoadingDbSnapshots.set(false);
        this.dbErrorMessage.set(
          err instanceof Error ? err.message : 'خطا در دریافت تاریخچه اسنپ‌شات‌های پایگاه‌داده'
        );
      },
    });
  }

  triggerDatabaseComparison(): void {
    const conn = this.selectedConnection();
    const v1Ts = this.selectedV1Ts();
    const v2Ts = this.selectedV2Ts();
    if (!conn || !v1Ts || !v2Ts) return;

    this.isLoadingDbDiff.set(true);
    this.dbErrorMessage.set(null);
    this.versionService.compareConnectionVersions(conn.id, v1Ts, v2Ts).subscribe({
      next: (res) => {
        this.dbDiff.set(res);
        this.isLoadingDbDiff.set(false);
      },
      error: (err: unknown) => {
        this.isLoadingDbDiff.set(false);
        this.dbErrorMessage.set(
          err instanceof Error ? err.message : 'خطا در مقایسه نسخه‌های اسکیمای پایگاه‌داده'
        );
      },
    });
  }

  toggleTableExpand(tableId: string): void {
    const next = new Set(this.expandedTableIds());
    if (next.has(tableId)) {
      next.delete(tableId);
    } else {
      next.add(tableId);
    }
    this.expandedTableIds.set(next);
  }

  isTableExpanded(tableId: string): boolean {
    return this.expandedTableIds().has(tableId);
  }

  formatAttrList(attrs: Record<string, any> | undefined): { key: string; val: any }[] {
    if (!attrs) return [];
    return Object.entries(attrs).map(([key, val]) => ({ key, val }));
  }

  loadTableVersions(tableId: string): void {
    if (typeof this.versionService.getVersions === 'function') {
      this.versionService.getVersions(tableId).subscribe({
        next: (versions) => {
          this.tableVersions.set(versions);
          if (versions && versions.length >= 2) {
            const firstVer = versions[0].version_num;
            const latestVer = versions[versions.length - 1].version_num;
            this.selectedV1.set(firstVer);
            this.selectedV2.set(latestVer);
            this.loadDiff(tableId, firstVer, latestVer);
          } else if (versions && versions.length === 1) {
            this.selectedV1.set(versions[0].version_num);
            this.selectedV2.set(versions[0].version_num);
            this.loadDiff(tableId, versions[0].version_num, versions[0].version_num);
          }
        },
        error: () => {},
      });
    }
  }

  onTableSelect(event: Event): void {
    const target = event.target as HTMLSelectElement;
    const tid = target.value;
    if (tid) {
      this.selectedTableId.set(tid);
      this.loadTableVersions(tid);
    }
  }

  onV1Select(event: Event): void {
    const target = event.target as HTMLSelectElement;
    this.selectedV1.set(Number(target.value));
  }

  onV2Select(event: Event): void {
    const target = event.target as HTMLSelectElement;
    this.selectedV2.set(Number(target.value));
  }

  triggerComparison(): void {
    const tid = this.selectedTableId();
    const ver1 = this.selectedV1();
    const ver2 = this.selectedV2();
    if (tid && ver1 !== null && ver2 !== null) {
      this.loadDiff(tid, ver1, ver2);
    }
  }

  loadDiff(tableId: string, v1: number, v2: number): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.versionService.compareVersions(tableId, v1, v2).subscribe({
      next: (result) => {
        this.fetchedDiff.set(result);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در مقایسه نسخه‌های اسکیما';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  setViewMode(mode: 'side-by-side' | 'categorized'): void {
    this.activeView.set(mode);
  }

  getAttributeDiffs(mod: ModifiedColumnItem): { attribute: string; v1_val: any; v2_val: any }[] {
    const keys = new Set([
      ...Object.keys(mod.v1_attributes || {}),
      ...Object.keys(mod.v2_attributes || {}),
    ]);
    return Array.from(keys).map((key) => ({
      attribute: key,
      v1_val: mod.v1_attributes?.[key],
      v2_val: mod.v2_attributes?.[key],
    }));
  }
}

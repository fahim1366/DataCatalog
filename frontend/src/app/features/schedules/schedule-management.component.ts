import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule, ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { forkJoin } from 'rxjs';
import { cronValidator } from '../connections/validators/cron.validator';
import { DatabaseConnection } from '../connections/models/connection.models';
import { CategoryTreeNode } from '../catalog/models/catalog.models';

export interface DatabaseNodeItem {
  id: string;
  name: string;
  engine_type: string;
  database_name: string;
  category_name?: string;
  host?: string;
  port?: number;
  owner?: string | null;
  is_active?: boolean;
}

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

export interface SyncScheduleData {
  id: string;
  connection_id: string;
  cron_expression: string;
  is_enabled: boolean;
  last_run_at?: string | null;
  last_run_status?: string | null;
  last_run_message?: string | null;
  next_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CronPreset {
  label: string;
  expression: string;
  description: string;
}

@Component({
  selector: 'app-schedule-management',
  standalone: true,
  imports: [CommonModule, FormsModule, ReactiveFormsModule],
  templateUrl: './schedule-management.component.html',
  styleUrls: ['./schedule-management.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ScheduleManagementComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly fb = inject(FormBuilder);

  // States
  readonly isLoadingTree = signal<boolean>(false);
  readonly isLoadingSchedule = signal<boolean>(false);
  readonly isSavingSchedule = signal<boolean>(false);
  readonly isTriggeringSync = signal<boolean>(false);

  // Backend state for categories & connections
  readonly rawCategories = signal<CategoryTreeNode[]>([]);
  readonly connections = signal<DatabaseConnection[]>([]);

  // Multi-criteria tree filtering (matching DbUserMonitorComponent and CatalogExplorerComponent)
  readonly filterConnectionName = signal<string>('');
  readonly filterDatabaseName = signal<string>('');
  readonly filterHost = signal<string>('');
  readonly filterOwner = signal<string>('');
  readonly filterEngineType = signal<string>('');

  // Tree expansion state
  readonly expandedCategoryIds = signal<Set<string>>(new Set<string>());

  // Check whether any tree filter is active
  readonly hasActiveTreeFilters = computed<boolean>(() => {
    return Boolean(
      this.filterConnectionName().trim() ||
      this.filterDatabaseName().trim() ||
      this.filterHost().trim() ||
      this.filterOwner().trim() ||
      this.filterEngineType()
    );
  });

  // Predicate to verify if a connection matches all active multi-criteria filters
  matchesConnection(conn: DatabaseConnection): boolean {
    const nameQ = this.filterConnectionName().trim().toLowerCase();
    if (nameQ && !conn.name.toLowerCase().includes(nameQ)) {
      return false;
    }

    const dbNameQ = this.filterDatabaseName().trim().toLowerCase();
    if (dbNameQ && !conn.database_name.toLowerCase().includes(dbNameQ)) {
      return false;
    }

    const hostQ = this.filterHost().trim().toLowerCase();
    if (hostQ && !conn.host.toLowerCase().includes(hostQ)) {
      return false;
    }

    const ownerQ = this.filterOwner().trim().toLowerCase();
    if (ownerQ && (!conn.owner || !conn.owner.toLowerCase().includes(ownerQ))) {
      return false;
    }

    const engine = this.filterEngineType();
    if (engine && conn.engine_type.toUpperCase() !== engine.toUpperCase()) {
      return false;
    }

    return true;
  }

  // Hierarchical Category -> Subcategories -> Databases Tree
  readonly displayTree = computed<DbTreeCategoryNode[]>(() => {
    const rawCategories = this.rawCategories();
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
  readonly totalFilteredDatabasesCount = computed<number>(() => {
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

  // Selected Database & Schedule
  readonly selectedDatabase = signal<DatabaseNodeItem | null>(null);
  readonly currentSchedule = signal<SyncScheduleData | null>(null);

  // Feedback Toasts
  readonly toastMessage = signal<string | null>(null);
  readonly toastType = signal<'success' | 'error'>('success');
  private toastTimer: any = null;

  // Cron Presets
  readonly cronPresets: CronPreset[] = [
    { label: 'هر ۶ ساعت', expression: '0 */6 * * *', description: 'اجرا هر ۶ ساعت در دقیقه صفر' },
    { label: 'روزانه (۲ بامداد)', expression: '0 2 * * *', description: 'هر شب ساعت ۲:۰۰ بامداد' },
    { label: 'هفتگی (جمعه‌ها)', expression: '0 1 * * 5', description: 'جمعه‌ها ساعت ۱:۰۰ بامداد' },
    { label: 'ماهانه (روز اول)', expression: '0 0 1 * *', description: 'ابتدای هر ماه تقویمی ساعت ۰۰:۰۰' },
    { label: 'هر ۱۵ دقیقه', expression: '*/15 * * * *', description: 'اجرا هر ۱۵ دقیقه یک‌بار (پایش مداوم)' },
  ];

  // Form
  readonly scheduleForm = this.fb.group({
    cron_expression: ['', [Validators.required, cronValidator()]],
    is_enabled: [true],
  });

  readonly currentCronExpression = signal<string>('');

  constructor() {
    this.scheduleForm.controls.cron_expression.valueChanges.subscribe((val) => {
      this.currentCronExpression.set(val || '');
    });
  }

  // Human readable live Persian preview of cron
  readonly cronHumanPreview = computed<string>(() => {
    const expr = (this.currentCronExpression().trim() || this.scheduleForm.controls.cron_expression.value?.trim()) || '';
    if (!expr) return 'عبارت زمان‌بندی را وارد نمایید.';

    const matched = this.cronPresets.find((p) => p.expression === expr);
    if (matched) return matched.description;

    const parts = expr.split(/\s+/);
    if (parts.length !== 5) return 'فرمت کرون باید دقیقاً از ۵ بخش مجزا با فاصله تشکیل شده باشد.';

    const [min, hour, dom, month, dow] = parts;
    if (min === '0' && hour.startsWith('*/')) {
      return `اجرا هر ${hour.replace('*/', '')} ساعت در ابتدای ساعت`;
    }
    if (min.startsWith('*/') && hour === '*' && dom === '*' && month === '*' && dow === '*') {
      return `اجرا هر ${min.replace('*/', '')} دقیقه یک‌بار در تمام ساعات`;
    }
    if (!isNaN(Number(min)) && !isNaN(Number(hour)) && dom === '*' && month === '*' && dow === '*') {
      return `اجرا هر روز راس ساعت ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
    }
    if (!isNaN(Number(min)) && !isNaN(Number(hour)) && dom === '*' && month === '*' && !isNaN(Number(dow))) {
      const days = ['یک‌شنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه', 'شنبه'];
      const dayName = days[Number(dow)] || `روز ${dow}`;
      return `اجرا در ${dayName}‌ها راس ساعت ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
    }
    return `اجرا بر اساس الگوی اختصاصی: ${expr}`;
  });

  ngOnInit(): void {
    this.loadCategoryTree();
  }

  loadCategoryTree(): void {
    this.isLoadingTree.set(true);
    forkJoin({
      categories: this.http.get<CategoryTreeNode[]>('/api/v1/categories/tree'),
      connections: this.http.get<DatabaseConnection[]>('/api/v1/connections'),
    }).subscribe({
      next: ({ categories, connections }) => {
        this.rawCategories.set(categories || []);
        this.connections.set(connections || []);
        this.isLoadingTree.set(false);

        // Auto-expand all top-level categories and uncategorized branch initially
        const initialExpanded = new Set<string>();
        (categories || []).forEach((c) => initialExpanded.add(c.id));
        initialExpanded.add('uncategorized');
        this.expandedCategoryIds.set(initialExpanded);

        // Auto-select first connection if any
        if (connections && connections.length > 0) {
          const firstConn = connections[0];
          const cat = (categories || []).find((c) => c.id === firstConn.category_id);
          this.selectConnection(firstConn, cat?.name);
        }
      },
      error: () => {
        this.isLoadingTree.set(false);
        this.showToast('خطا در دریافت ساختار پایگاه‌های داده', 'error');
      },
    });
  }

  isCategoryExpanded(node: DbTreeCategoryNode): boolean {
    if (this.hasActiveTreeFilters()) {
      return node.hasMatchingDescendants || node.connections.length > 0;
    }
    return this.expandedCategoryIds().has(node.id);
  }

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

  clearTreeFilters(): void {
    this.filterConnectionName.set('');
    this.filterDatabaseName.set('');
    this.filterHost.set('');
    this.filterOwner.set('');
    this.filterEngineType.set('');
  }

  selectConnection(conn: DatabaseConnection, categoryName?: string): void {
    const dbItem: DatabaseNodeItem = {
      id: conn.id,
      name: conn.name,
      engine_type: conn.engine_type,
      database_name: conn.database_name,
      category_name: categoryName || 'بدون دسته',
      host: conn.host,
      port: conn.port,
      owner: conn.owner,
      is_active: conn.is_active,
    };
    this.selectDatabase(dbItem);
  }

  selectDatabase(db: DatabaseNodeItem): void {
    this.selectedDatabase.set(db);
    this.isLoadingSchedule.set(true);
    this.http.get<SyncScheduleData>(`/api/v1/connections/${db.id}/schedule`).subscribe({
      next: (schedule) => {
        this.currentSchedule.set(schedule);
        const cron = schedule.cron_expression || '0 2 * * *';
        this.scheduleForm.patchValue({
          cron_expression: cron,
          is_enabled: schedule.is_enabled ?? true,
        });
        this.currentCronExpression.set(cron);
        this.isLoadingSchedule.set(false);
      },
      error: () => {
        this.isLoadingSchedule.set(false);
        this.showToast('خطا در بارگذاری جزئیات زمان‌بندی پایگاه داده', 'error');
      },
    });
  }

  applyPreset(preset: CronPreset): void {
    this.scheduleForm.patchValue({
      cron_expression: preset.expression,
    });
    this.currentCronExpression.set(preset.expression);
    this.scheduleForm.controls.cron_expression.markAsDirty();
  }

  saveSchedule(): void {
    const db = this.selectedDatabase();
    if (!db || this.scheduleForm.invalid) {
      this.scheduleForm.markAllAsTouched();
      return;
    }

    this.isSavingSchedule.set(true);
    const payload = {
      cron_expression: this.scheduleForm.controls.cron_expression.value?.trim(),
      is_enabled: this.scheduleForm.controls.is_enabled.value ?? true,
    };

    this.http.put<SyncScheduleData>(`/api/v1/connections/${db.id}/schedule`, payload).subscribe({
      next: (updated) => {
        this.currentSchedule.set(updated);
        this.isSavingSchedule.set(false);
        this.showToast('تنظیمات زمان‌بندی با موفقیت ذخیره شد.', 'success');
      },
      error: (err) => {
        this.isSavingSchedule.set(false);
        const msg = err?.error?.detail || 'خطا در ذخیره تنظیمات زمان‌بندی';
        this.showToast(msg, 'error');
      },
    });
  }

  triggerSyncNow(): void {
    const db = this.selectedDatabase();
    if (!db) return;

    this.isTriggeringSync.set(true);
    this.http.post<SyncScheduleData>(`/api/v1/connections/${db.id}/sync`, {}).subscribe({
      next: (updated) => {
        this.currentSchedule.set(updated);
        this.isTriggeringSync.set(false);
        this.showToast(`همگام‌سازی متاداده پایگاه داده «${db.name}» با موفقیت انجام شد.`, 'success');
      },
      error: (err) => {
        this.isTriggeringSync.set(false);
        const msg = err?.error?.detail || 'خطا در اجرای فرآیند همگام‌سازی';
        this.showToast(msg, 'error');
      },
    });
  }

  showToast(message: string, type: 'success' | 'error' = 'success'): void {
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastMessage.set(message);
    this.toastType.set(type);
    this.toastTimer = setTimeout(() => this.toastMessage.set(null), 4500);
  }

  closeToast(): void {
    this.toastMessage.set(null);
  }
}

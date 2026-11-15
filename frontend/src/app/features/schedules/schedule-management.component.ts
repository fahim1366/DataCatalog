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
import { cronValidator } from '../connections/validators/cron.validator';

export interface DatabaseNodeItem {
  id: string;
  name: string;
  engine_type: string;
  database_name: string;
  category_name?: string;
}

export interface CategoryHierarchyNode {
  id: string;
  name: string;
  parent_id?: string | null;
  databases: DatabaseNodeItem[];
  children: CategoryHierarchyNode[];
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

  // Tree data
  readonly rawCategoryTree = signal<CategoryHierarchyNode[]>([]);
  readonly treeSearchTerm = signal<string>('');
  readonly expandedCategoryIds = signal<Set<string>>(new Set());

  // Filtered Tree Computed
  readonly filteredTree = computed(() => {
    const term = this.treeSearchTerm().trim().toLowerCase();
    const tree = this.rawCategoryTree();
    if (!term) return tree;

    const filterNode = (node: CategoryHierarchyNode): CategoryHierarchyNode | null => {
      const nodeMatches = node.name.toLowerCase().includes(term);
      const filteredDbs = (node.databases || []).filter(
        (db) =>
          db.name.toLowerCase().includes(term) ||
          db.database_name.toLowerCase().includes(term) ||
          db.engine_type.toLowerCase().includes(term)
      );
      const filteredChildren = (node.children || [])
        .map((child) => filterNode(child))
        .filter((child): child is CategoryHierarchyNode => child !== null);

      if (nodeMatches || filteredDbs.length > 0 || filteredChildren.length > 0) {
        return {
          ...node,
          databases: nodeMatches ? node.databases : filteredDbs,
          children: filteredChildren,
        };
      }
      return null;
    };

    return tree.map((n) => filterNode(n)).filter((n): n is CategoryHierarchyNode => n !== null);
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

  // Human readable live Persian preview of cron
  readonly cronHumanPreview = computed<string>(() => {
    const expr = this.scheduleForm.controls.cron_expression.value?.trim();
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
    this.http.get<CategoryHierarchyNode[]>('/api/v1/categories/tree').subscribe({
      next: (data) => {
        this.rawCategoryTree.set(data || []);
        this.isLoadingTree.set(false);

        // Auto-expand root categories and select first available database
        const rootIds = new Set<string>();
        let firstDb: DatabaseNodeItem | null = null;

        const findFirst = (nodes: CategoryHierarchyNode[]) => {
          for (const n of nodes) {
            rootIds.add(n.id);
            if (!firstDb && n.databases && n.databases.length > 0) {
              firstDb = { ...n.databases[0], category_name: n.name };
            }
            if (n.children) {
              findFirst(n.children);
            }
          }
        };

        findFirst(data || []);
        this.expandedCategoryIds.set(rootIds);

        if (firstDb) {
          this.selectDatabase(firstDb);
        }
      },
      error: () => {
        this.isLoadingTree.set(false);
        this.showToast('خطا در دریافت ساختار درختی پایگاه‌های داده', 'error');
      },
    });
  }

  toggleCategory(id: string): void {
    const current = new Set(this.expandedCategoryIds());
    if (current.has(id)) {
      current.delete(id);
    } else {
      current.add(id);
    }
    this.expandedCategoryIds.set(current);
  }

  selectDatabase(db: DatabaseNodeItem): void {
    this.selectedDatabase.set(db);
    this.isLoadingSchedule.set(true);
    this.http.get<SyncScheduleData>(`/api/v1/connections/${db.id}/schedule`).subscribe({
      next: (schedule) => {
        this.currentSchedule.set(schedule);
        this.scheduleForm.patchValue({
          cron_expression: schedule.cron_expression || '0 2 * * *',
          is_enabled: schedule.is_enabled ?? true,
        });
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

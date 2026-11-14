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
import { VersionService } from './services/version.service';
import {
  ColumnSnapshot,
  DiffSideBySideRow,
  ModifiedColumnItem,
  TableVersionItem,
  VersionDiffResponse,
} from './models/version.models';

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

  // Toolbar & Table Selection State
  readonly availableTables = signal<Array<{ id: string; name: string; schema_name?: string }>>([]);
  readonly selectedTableId = signal<string>('');
  readonly tableVersions = signal<TableVersionItem[]>([]);
  readonly selectedV1 = signal<number>(1);
  readonly selectedV2 = signal<number>(2);

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
      // Auto-fetch authorized tables and versions on standalone /versions route
      if (typeof this.versionService.getTables === 'function') {
        this.versionService.getTables().subscribe({
          next: (tables) => {
            if (tables && tables.length > 0) {
              this.availableTables.set(tables);
              // Prefer dim_customers if available, otherwise first table
              const defaultTable =
                tables.find((t) => t.name === 'dim_customers') || tables[0];
              this.selectedTableId.set(defaultTable.id);
              this.loadTableVersions(defaultTable.id);
            }
          },
          error: () => {},
        });
      }
    }
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

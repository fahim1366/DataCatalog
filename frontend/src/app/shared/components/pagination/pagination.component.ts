import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-pagination',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (totalItems() > 0) {
      <div class="table-pagination-footer" dir="rtl">
        <div class="pagination-info">
          نمایش {{ displayStart() }} تا {{ displayEnd() }} از {{ totalItems() }} مورد
        </div>

        <div class="pagination-controls">
          <button
            type="button"
            class="page-nav-btn"
            [disabled]="safePage() === 1"
            (click)="goToPage(1)"
            title="صفحه نخست"
            aria-label="صفحه نخست"
          >
            <i class="pi pi-angle-double-right"></i>
          </button>
          <button
            type="button"
            class="page-nav-btn"
            [disabled]="safePage() === 1"
            (click)="goToPage(safePage() - 1)"
            title="صفحه قبلی"
            aria-label="صفحه قبلی"
          >
            <i class="pi pi-angle-right"></i>
          </button>

          @for (p of visiblePages(); track p) {
            <button
              type="button"
              class="page-num-btn"
              [class.active]="safePage() === p"
              (click)="goToPage(p)"
            >
              {{ p }}
            </button>
          }

          <button
            type="button"
            class="page-nav-btn"
            [disabled]="safePage() === totalPages()"
            (click)="goToPage(safePage() + 1)"
            title="صفحه بعدی"
            aria-label="صفحه بعدی"
          >
            <i class="pi pi-angle-left"></i>
          </button>
          <button
            type="button"
            class="page-nav-btn"
            [disabled]="safePage() === totalPages()"
            (click)="goToPage(totalPages())"
            title="صفحه آخر"
            aria-label="صفحه آخر"
          >
            <i class="pi pi-angle-double-left"></i>
          </button>
        </div>

        <div class="pagination-size-selector">
          <label>تعداد در صفحه:</label>
          <select
            [ngModel]="pageSize()"
            (ngModelChange)="onPageSizeChange($event)"
            class="page-size-select"
          >
            @for (sz of pageSizes(); track sz) {
              <option [ngValue]="sz">{{ sz }}</option>
            }
          </select>
        </div>
      </div>
    }
  `,
  styles: [
    `
      .table-pagination-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        flex-wrap: wrap;
        padding: 0.75rem 1rem;
        border-top: 1px solid #e2e8f0;
        background: #f8fafc;
        font-size: 0.8rem;
        color: #475569;
      }
      .pagination-controls {
        display: flex;
        align-items: center;
        gap: 0.25rem;
      }
      .page-nav-btn,
      .page-num-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 2rem;
        height: 2rem;
        padding: 0 0.4rem;
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        color: #334155;
        font-size: 0.8rem;
        font-family: inherit;
        cursor: pointer;
      }
      .page-nav-btn:hover:not(:disabled),
      .page-num-btn:hover {
        background: #e2e8f0;
      }
      .page-nav-btn:disabled {
        opacity: 0.45;
        cursor: not-allowed;
      }
      .page-num-btn.active {
        background: #2563eb;
        border-color: #2563eb;
        color: #ffffff;
        font-weight: 700;
      }
      .pagination-size-selector {
        display: flex;
        align-items: center;
        gap: 0.5rem;
      }
      .page-size-select {
        padding: 0.25rem 0.5rem;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #ffffff;
        color: #1e293b;
        font-size: 0.8rem;
        font-family: inherit;
        cursor: pointer;
      }
    `,
  ],
})
export class PaginationComponent {
  totalItems = input.required<number>();
  currentPage = input.required<number>();
  pageSize = input.required<number>();
  pageSizes = input<number[]>([10, 20, 50]);

  pageChange = output<number>();
  pageSizeChange = output<number>();

  totalPages = computed(() => Math.max(1, Math.ceil(this.totalItems() / Math.max(1, this.pageSize()))));
  safePage = computed(() => Math.min(Math.max(1, this.currentPage()), this.totalPages()));

  displayStart = computed(() =>
    this.totalItems() === 0 ? 0 : (this.safePage() - 1) * this.pageSize() + 1
  );
  displayEnd = computed(() => Math.min(this.safePage() * this.pageSize(), this.totalItems()));

  visiblePages = computed(() => {
    const total = this.totalPages();
    const cur = this.safePage();
    const pages: number[] = [];
    const start = Math.max(1, cur - 2);
    const end = Math.min(total, cur + 2);
    for (let p = start; p <= end; p++) {
      pages.push(p);
    }
    return pages;
  });

  goToPage(page: number): void {
    const clamped = Math.min(Math.max(1, page), this.totalPages());
    if (clamped !== this.safePage()) {
      this.pageChange.emit(clamped);
    }
  }

  onPageSizeChange(newSize: number): void {
    const parsed = Number(newSize);
    if (parsed > 0) {
      this.pageSizeChange.emit(parsed);
    }
  }
}

import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnInit,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  FormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import {
  ConnectionCreateRequest,
  DatabaseConnection,
  DatabaseEngine,
} from './models/connection.models';
import { cronValidator } from './validators/cron.validator';
import { ConnectionService } from './services/connection.service';
import { CategoryTreeNode } from '../catalog/models/catalog.models';

export interface FlattenedCategoryNode {
  node: CategoryTreeNode;
  level: number;
  isLeaf: boolean;
  parentPath: string;
}

@Component({
  selector: 'app-connection-dialog',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  template: `
    <div class="modal-backdrop" (click)="onBackdropClick($event)">
      <div class="modal-card" role="dialog" aria-labelledby="dialog-title" aria-modal="true">
        <header class="modal-header">
          <h2 id="dialog-title">
            <i class="pi" [ngClass]="connectionToEdit ? 'pi-pencil' : 'pi-plus-circle'"></i>
            <span>{{ connectionToEdit ? 'ویرایش پایگاه‌داده (Edit Database)' : 'ثبت پایگاه‌داده جدید (Register Database)' }}</span>
          </h2>
          <button type="button" class="btn-close" (click)="cancel.emit()" aria-label="بستن پنجره" title="بستن">
            <i class="pi pi-times"></i>
          </button>
        </header>

        

        <form [formGroup]="form" (ngSubmit)="onSubmit()" class="connection-form">
          <!-- Database Title -->
          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="name">عنوان پایگاه‌داده (Database Title / Name) <span class="required">*</span></label>
              <input
                id="name"
                type="text"
                class="form-control"
                formControlName="name"
                placeholder="مثال: پایگاه داده تحلیلی یا انبار داده مشتریان"
              />
              @if (form.controls.name.touched && form.controls.name.invalid) {
                <div class="field-error">عنوان پایگاه‌داده الزامی است.</div>
              }
            </div>
          </div>

          <!-- Hierarchical Topic Tree Selector (Only Leaves Selectable) -->
          <div class="form-row">
            <div class="form-group col-span-2 topic-section">
              <div class="topic-section-header">
                <label class="topic-label">
                  انتخاب دیتابیس
                 
                </label>

                @if (selectedCategoryName()) {
                  <div class="selected-topic-chip">
                    <i class="pi pi-file chip-leaf-icon"></i>
                    <span class="chip-text">موضوع انتخاب‌شده: <strong>{{ selectedCategoryName() }}</strong></span>
                    <button
                      type="button"
                      class="btn-clear-topic"
                      (click)="clearCategorySelection()"
                      title="حذف انتساب موضوع"
                      aria-label="حذف انتساب موضوع"
                    >
                      <i class="pi pi-times"></i>
                    </button>
                  </div>
                }
              </div>

              <div class="topic-tree-picker">
                <div class="topic-search-bar">
                  <div class="topic-search-wrapper">
                    <i class="pi pi-search topic-search-icon"></i>
                    <input
                      type="text"
                      class="topic-search-input"
                      placeholder="جستجو "
                      [value]="categorySearchTerm()"
                      (input)="onSearchInput($event)"
                    />
                  </div>
                  @if (isLoadingCategories()) {
                    <span class="loading-hint">
                      <i class="pi pi-spin pi-spinner"></i>
                      <span>در حال بارگذاری درخت موضوعات...</span>
                    </span>
                  }
                </div>

                <div class="topic-tree-viewport">
                  @if (flattenedCategoryList().length === 0 && !isLoadingCategories()) {
                    <div class="empty-tree-hint">
                      هیچ موضوع یا برگی یافت نشد.
                    </div>
                  }

                  @for (item of flattenedCategoryList(); track item.node.id) {
                    <div
                      class="tree-node-row"
                      [class.is-leaf]="item.isLeaf"
                      [class.is-parent]="!item.isLeaf"
                      [class.is-selected]="form.controls.category_id.value === item.node.id"
                      [style.padding-right.px]="12 + (item.level * 22)"
                      (click)="onNodeClick(item)"
                    >
                      @if (!item.isLeaf) {
                        <!-- Parent Node (Branch / Folder) -->
                        <button
                          type="button"
                          class="btn-node-toggle"
                          (click)="toggleExpanded(item.node.id); $event.stopPropagation()"
                          [attr.aria-label]="expandedNodeIds().has(item.node.id) ? 'بستن زیرشاخه' : 'باز کردن زیرشاخه'"
                        >
                          <i
                            class="pi"
                            [ngClass]="expandedNodeIds().has(item.node.id) ? 'pi-chevron-down' : 'pi-chevron-left'"
                          ></i>
                        </button>
                        <i
                          class="pi node-icon folder-icon"
                          [ngClass]="expandedNodeIds().has(item.node.id) ? 'pi-folder-open' : 'pi-folder'"
                        ></i>
                        <span class="node-name parent-name">{{ item.node.name }}</span>
                        <!-- <span class="node-type-badge parent-badge">شاخه والد (غیرقابل انتخاب)</span> -->
                      } @else {
                        <!-- Leaf Node (Selectable) -->
                        <span class="leaf-radio-circle" [class.checked]="form.controls.category_id.value === item.node.id"></span>
                        <i class="pi pi-file node-icon leaf-icon"></i>
                        <span class="node-name leaf-name">{{ item.node.name }}</span>

                        @if (form.controls.category_id.value === item.node.id) {
                          <span class="selected-pill">
                            <i class="pi pi-check"></i>
                            <span>انتخاب شد</span>
                          </span>
                        }
                      }
                    </div>
                  }
                </div>
              </div>
            </div>
          </div>

          <!-- Engine Type, Host, Port -->
          <div class="form-row grid-3">
            <div class="form-group">
              <label for="engine_type">  نوع دیتابیس  <span class="required">*</span></label>
              <select
                id="engine_type"
                class="form-control"
                formControlName="engine_type"
                (change)="onEngineChange()"
              >
                @for (engine of supportedEngines; track engine) {
                  <option [value]="engine">{{ engine }}</option>
                }
              </select>
            </div>

            <div class="form-group col-span-2">
              <label for="host">آدرس سرور(Host / Address) <span class="required">*</span></label>
              <input
                id="host"
                type="text"
                class="form-control font-mono"
                formControlName="host"
                placeholder="hostname or IP"
              />
              @if (form.controls.host.touched && form.controls.host.invalid) {
                <div class="field-error">آدرس هاست الزامی است (Host is required).</div>
              }
            </div>
          </div>

          <!-- Port, Database Name, Default Schema -->
          <div class="form-row grid-3">
            <div class="form-group">
              <label for="port">پورت اتصال (Port) <span class="required">*</span></label>
              <input
                id="port"
                type="number"
                class="form-control font-mono"
                formControlName="port"
              />
              @if (form.controls.port.touched && form.controls.port.invalid) {
                <div class="field-error">پورت معتبر (۱ تا ۶۵۵۳۵) الزامی است.</div>
              }
            </div>

            <div class="form-group">
              <label for="database_name">نام دیتابیس (Database Name) <span class="required">*</span></label>
              <input
                id="database_name"
                type="text"
                class="form-control font-mono"
                formControlName="database_name"
                placeholder="Target database/catalog"
              />
              @if (form.controls.database_name.touched && form.controls.database_name.invalid) {
                <div class="field-error">نام پایگاه‌داده الزامی است.</div>
              }
            </div>

            <div class="form-group">
              <label for="default_schema">اسکیمای پیش‌فرض (Default Schema)</label>
              <input
                id="default_schema"
                type="text"
                class="form-control font-mono"
                formControlName="default_schema"
                placeholder="مثال: public یا dbo"
              />
            </div>
          </div>

          <!-- Username & Password -->
          <div class="form-row grid-2">
            <div class="form-group">
              <label for="username">نام کاربری اتصال (Username) <span class="required">*</span></label>
              <input
                id="username"
                type="text"
                class="form-control font-mono"
                formControlName="username"
                autocomplete="off"
              />
              @if (form.controls.username.touched && form.controls.username.invalid) {
                <div class="field-error">نام کاربری الزامی است.</div>
              }
            </div>

            <div class="form-group">
              <label for="password">
                {{ connectionToEdit ? 'رمز عبور جدید (اختیاری در ویرایش)' : 'رمز عبور (Password)' }}
                @if (!connectionToEdit) { <span class="required">*</span> }
              </label>
              <input
                id="password"
                type="password"
                class="form-control font-mono"
                formControlName="password"
                autocomplete="new-password"
                [placeholder]="connectionToEdit ? 'در صورت عدم تغییر، خالی بگذارید' : 'رمز عبور امن دیتابیس'"
              />
              @if (form.controls.password.touched && form.controls.password.invalid) {
                <div class="field-error">رمز عبور الزامی است.</div>
              }
            </div>
          </div>

          <!-- Owner -->
          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="owner">مالک / تیم فنی مسئول (Owner / Team)</label>
              <input
                id="owner"
                type="text"
                class="form-control"
                formControlName="owner"
                placeholder="مثال: data-platform یا bi-team"
              />
            </div>
          </div>

          <!-- Cron Expression Sync Schedule -->
          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="cron_expression">برنامه زمان‌بندی همگام‌سازی (Sync Schedule - 5-part Cron)</label>
              <input
                id="cron_expression"
                type="text"
                class="form-control font-mono"
                formControlName="cron_expression"
                placeholder="مثال: 0 2 * * * (هر شب ساعت ۲ بامداد)"
              />
              <span class="cron-hint">الگوی استاندارد کرون: &lt;دقیقه&gt; &lt;ساعت&gt; &lt;روز ماه&gt; &lt;ماه&gt; &lt;روز هفته&gt;</span>

              @if (form.controls.cron_expression.errors?.['invalidCron']) {
                <div class="cron-error-feedback">
                  <i class="pi pi-times-circle"></i>
                  <span>{{ form.controls.cron_expression.errors?.['invalidCron']?.message }}</span>
                </div>
              } @else if (form.controls.cron_expression.value && form.controls.cron_expression.valid) {
                <div class="cron-valid-feedback">
                  <div class="cron-valid-line">
                    <i class="pi pi-check-circle"></i>
                    <span>الگوی معتبر زمان‌بندی (Valid 5-part cron schedule)</span>
                  </div>
                  <div class="cron-human-summary">{{ getCronHumanSummary(form.controls.cron_expression.value) }}</div>
                </div>
              }
            </div>
          </div>

          @if (errorMessage()) {
            <div class="alert-error">
              <i class="pi pi-exclamation-triangle"></i>
              <span>{{ errorMessage() }}</span>
            </div>
          }

          <footer class="modal-footer">
            <button
              type="button"
              class="btn-secondary"
              (click)="cancel.emit()"
            >
              انصراف (Cancel)
            </button>
            <button
              type="submit"
              class="btn-primary btn-with-icon"
              [disabled]="form.invalid || isSubmitting()"
            >
              @if (isSubmitting()) {
                <i class="pi pi-spin pi-spinner"></i>
                <span>در حال ذخیره و اتصال...</span>
              } @else if (connectionToEdit) {
                <i class="pi pi-check"></i>
                <span>ذخیره تغییرات پایگاه‌داده (Update Database)</span>
              } @else {
                <i class="pi pi-check"></i>
                <span>ثبت پایگاه‌داده (Register Database)</span>
              }
            </button>
          </footer>
        </form>
      </div>
    </div>
  `,
  styles: [`
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.6);
      backdrop-filter: blur(4px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 1rem;
      direction: rtl;
      font-family: var(--font-family-sans, 'Vazirmatn', sans-serif);
    }
    .modal-card {
      background: #ffffff;
      border-radius: 10px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
      width: 100%;
      max-width: 720px;
      max-height: 90vh;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 1.25rem 1.5rem;
      border-bottom: 1px solid #e2e8f0;
    }
    .modal-header h2 {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin: 0;
      font-size: 1.15rem;
      font-weight: 700;
      color: #0f172a;
    }
    .modal-header h2 .pi {
      color: #2563eb;
      font-size: 1.05rem;
    }
    .dialog-info-banner {
      display: flex;
      align-items: flex-start;
      gap: 0.5rem;
      background-color: #eff6ff;
      border: 1px solid #bfdbfe;
      color: #1e40af;
      padding: 10px 16px;
      font-size: 0.85rem;
      font-weight: 500;
      line-height: 1.5;
    }
    .dialog-info-banner .pi {
      margin-top: 0.2rem;
      flex-shrink: 0;
    }
    .btn-close {
      background: none;
      border: 1px solid #cbd5e1;
      border-radius: 50%;
      font-size: 0.8rem;
      cursor: pointer;
      color: #64748b;
      width: 30px;
      height: 30px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .btn-close:hover {
      background: #f1f5f9;
      color: #0f172a;
    }
    .connection-form {
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }
    .form-row {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1rem;
    }
    .form-row.grid-3 {
      grid-template-columns: 1fr 1fr 1fr;
    }
    .form-row.grid-2 {
      grid-template-columns: 1fr 1fr;
    }
    .col-span-2 {
      grid-column: span 2;
    }
    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    .form-group label {
      font-size: 0.82rem;
      font-weight: 600;
      color: #334155;
    }
    .required {
      color: #ef4444;
    }
    .form-control {
      padding: 0.5rem 0.75rem;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      font-size: 0.85rem;
      color: #0f172a;
      background: #ffffff;
      outline: none;
      transition: border-color 0.15s;
    }
    .form-control:focus {
      border-color: #2563eb;
      box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
    }
    .font-mono {
      font-family: var(--font-family-mono, monospace);
    }
    .field-error {
      color: #ef4444;
      font-size: 0.75rem;
    }

    /* Topic Section & Tree Picker */
    .topic-section {
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 1rem;
    }
    .topic-section-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .topic-label {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.85rem;
      font-weight: 700;
      color: #1e293b;
    }
    .leaf-rule-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.75rem;
      font-weight: 500;
      background: #fef3c7;
      color: #92400e;
      padding: 2px 8px;
      border-radius: 9999px;
      border: 1px solid #fde68a;
    }
    .selected-topic-chip {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      background: #ecfdf5;
      color: #065f46;
      border: 1px solid #a7f3d0;
      border-radius: 9999px;
      padding: 3px 10px;
      font-size: 0.8rem;
    }
    .chip-leaf-icon {
      font-size: 0.85rem;
    }
    .btn-clear-topic {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: none;
      border: none;
      cursor: pointer;
      color: #047857;
      font-size: 0.7rem;
      padding: 0 2px;
    }
    .btn-clear-topic:hover {
      color: #dc2626;
    }
    .topic-tree-picker {
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      overflow: hidden;
    }
    .topic-search-bar {
      padding: 0.5rem 0.75rem;
      border-bottom: 1px solid #e2e8f0;
      background: #f1f5f9;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .topic-search-wrapper {
      position: relative;
      flex: 1;
    }
    .topic-search-icon {
      position: absolute;
      top: 50%;
      right: 0.6rem;
      transform: translateY(-50%);
      font-size: 0.75rem;
      color: #94a3b8;
      pointer-events: none;
    }
    .topic-search-input {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #cbd5e1;
      border-radius: 4px;
      padding: 0.35rem 1.9rem 0.35rem 0.6rem;
      font-size: 0.8rem;
      font-family: inherit;
      outline: none;
    }
    .topic-search-input:focus {
      border-color: #2563eb;
    }
    .loading-hint {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      font-size: 0.75rem;
      color: #64748b;
      white-space: nowrap;
    }
    .topic-tree-viewport {
      max-height: 180px;
      overflow-y: auto;
      padding: 0.35rem 0;
    }
    .empty-tree-hint {
      padding: 1rem;
      text-align: center;
      color: #64748b;
      font-size: 0.82rem;
    }
    .tree-node-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.45rem 0.75rem;
      cursor: pointer;
      transition: background-color 0.15s;
      user-select: none;
      font-size: 0.82rem;
    }
    .tree-node-row:hover {
      background: #f1f5f9;
    }
    .tree-node-row.is-parent {
      color: #334155;
      font-weight: 600;
    }
    .tree-node-row.is-parent:hover {
      background: #e2e8f0;
    }
    .tree-node-row.is-leaf {
      color: #0f172a;
    }
    .tree-node-row.is-leaf:hover {
      background: #e0f2fe;
    }
    .tree-node-row.is-selected {
      background: #dbeafe !important;
      border-right: 3px solid #2563eb;
    }
    .btn-node-toggle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: none;
      border: none;
      color: #64748b;
      font-size: 0.7rem;
      cursor: pointer;
      padding: 2px 4px;
    }
    .node-icon {
      font-size: 0.95rem;
    }
    .folder-icon {
      color: #f59e0b;
    }
    .leaf-icon {
      color: #16a34a;
    }
    .leaf-radio-circle {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      border: 2px solid #94a3b8;
      display: inline-block;
      flex-shrink: 0;
    }
    .leaf-radio-circle.checked {
      border-color: #2563eb;
      background: #2563eb;
      box-shadow: inset 0 0 0 2px #ffffff;
    }
    .node-name {
      flex: 1;
      text-align: right;
    }
    .parent-name {
      color: #1e293b;
    }
    .leaf-name {
      color: #0f172a;
    }
    .node-type-badge {
      font-size: 0.7rem;
      padding: 1px 6px;
      border-radius: 4px;
    }
    .parent-badge {
      background: #f1f5f9;
      color: #64748b;
      border: 1px solid #e2e8f0;
    }
    .leaf-badge {
      background: #f0fdf4;
      color: #166534;
      border: 1px solid #bbf7d0;
    }
    .selected-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.75rem;
      color: #1d4ed8;
      font-weight: 700;
      background: #eff6ff;
      border: 1px solid #bfdbfe;
      border-radius: 4px;
      padding: 2px 6px;
    }

    .cron-hint {
      color: #64748b;
      font-size: 0.75rem;
    }
    .cron-error-feedback {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      color: #dc2626;
      font-size: 0.75rem;
      font-weight: 500;
      margin-top: 0.25rem;
    }
    .cron-valid-feedback {
      color: #16a34a;
      font-size: 0.75rem;
      font-weight: 500;
      margin-top: 0.25rem;
    }
    .cron-valid-line {
      display: flex;
      align-items: center;
      gap: 0.35rem;
    }
    .cron-human-summary {
      color: #0f172a;
      font-size: 0.75rem;
      font-weight: 600;
      margin-top: 0.15rem;
    }
    .alert-error {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 1rem;
      background: #fef2f2;
      border: 1px solid #fecaca;
      border-radius: 6px;
      color: #991b1b;
      font-size: 0.85rem;
    }
    .modal-footer {
      display: flex;
      justify-content: flex-end;
      gap: 0.75rem;
      padding-top: 1rem;
      border-top: 1px solid #e2e8f0;
    }
    .btn-with-icon {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
    }
    .btn-secondary {
      padding: 0.5rem 1rem;
      background: #f1f5f9;
      color: #475569;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      font-family: inherit;
    }
    .btn-primary {
      padding: 0.5rem 1.25rem;
      background: #2563eb;
      color: #ffffff;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      font-family: inherit;
    }
    .btn-primary:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionDialogComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly connectionService = inject(ConnectionService);

  @Input() connectionToEdit: DatabaseConnection | null = null;
  @Input() preselectedCategoryId: string | null = null;

  @Output() readonly save = new EventEmitter<ConnectionCreateRequest>();
  @Output() readonly cancel = new EventEmitter<void>();

  readonly supportedEngines: DatabaseEngine[] = [
    'POSTGRESQL',
    'MSSQL',
    'ORACLE',
    'MYSQL',
    'CLICKHOUSE',
    'MONGODB',
  ];

  private readonly defaultPorts: Record<DatabaseEngine, number> = {
    POSTGRESQL: 5432,
    MSSQL: 1433,
    ORACLE: 1521,
    MYSQL: 3306,
    CLICKHOUSE: 8123,
    MONGODB: 27017,
  };

  readonly isSubmitting = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);

  // Category Tree Signals
  readonly rawCategories = signal<CategoryTreeNode[]>([]);
  readonly isLoadingCategories = signal<boolean>(false);
  readonly categorySearchTerm = signal<string>('');
  readonly expandedNodeIds = signal<Set<string>>(new Set<string>());
  readonly selectedCategoryName = signal<string | null>(null);

  readonly form = this.fb.group({
    name: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(1), Validators.maxLength(200)],
    }),
    category_id: this.fb.control<string | null>(null),
    engine_type: this.fb.control<DatabaseEngine>('POSTGRESQL', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    host: this.fb.control<string>('localhost', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(1), Validators.maxLength(255)],
    }),
    port: this.fb.control<number>(5432, {
      nonNullable: true,
      validators: [Validators.required, Validators.min(1), Validators.max(65535)],
    }),
    database_name: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(1), Validators.maxLength(255)],
    }),
    default_schema: this.fb.control<string | null>('public'),
    owner: this.fb.control<string>('admin', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(255)],
    }),
    username: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    password: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    cron_expression: this.fb.control<string | null>('', {
      validators: [cronValidator()],
    }),
  });

  // Flattened and filtered category list for display in the tree picker
  readonly flattenedCategoryList = computed<FlattenedCategoryNode[]>(() => {
    const raw = this.rawCategories();
    const expanded = this.expandedNodeIds();
    const term = this.categorySearchTerm().trim().toLowerCase();

    const result: FlattenedCategoryNode[] = [];

    const traverse = (nodes: CategoryTreeNode[], level: number, parentPath: string) => {
      for (const node of nodes) {
        const isLeaf = !node.children || node.children.length === 0;
        const currentPath = parentPath ? `${parentPath} > ${node.name}` : node.name;

        // If searching, show matching leaves or parents that contain matching children
        if (term) {
          const matchesSelf = node.name.toLowerCase().includes(term);
          const hasMatchingChild = this.hasMatchingDescendant(node, term);
          if (matchesSelf || hasMatchingChild) {
            result.push({ node, level, isLeaf, parentPath: currentPath });
            if (node.children && node.children.length > 0) {
              traverse(node.children, level + 1, currentPath);
            }
          }
        } else {
          result.push({ node, level, isLeaf, parentPath: currentPath });
          if (!isLeaf && expanded.has(node.id) && node.children) {
            traverse(node.children, level + 1, currentPath);
          }
        }
      }
    };

    traverse(raw, 0, '');
    return result;
  });

  ngOnInit(): void {
    this.loadCategoryTree();

    if (this.connectionToEdit) {
      const conn = this.connectionToEdit;
      this.form.patchValue({
        name: conn.name,
        category_id: conn.category_id || null,
        engine_type: conn.engine_type,
        host: conn.host,
        port: conn.port,
        database_name: conn.database_name,
        default_schema: conn.default_schema || '',
        owner: conn.owner,
        username: conn.masked_credentials?.['username'] || 'admin',
        password: '',
        cron_expression: conn.sync_schedule?.cron_expression || '',
      });
      if (conn.category_name) {
        this.selectedCategoryName.set(conn.category_name);
      }
      // In edit mode, password is optional
      this.form.controls.password.clearValidators();
      this.form.controls.password.updateValueAndValidity();
    } else if (this.preselectedCategoryId) {
      this.form.controls.category_id.setValue(this.preselectedCategoryId);
    }
  }

  loadCategoryTree(): void {
    this.isLoadingCategories.set(true);
    this.connectionService.getCategoryTree().subscribe({
      next: (tree) => {
        this.rawCategories.set(tree);
        this.isLoadingCategories.set(false);

        // Auto-expand parents if category is already selected
        const currentCatId = this.form.controls.category_id.value;
        if (currentCatId) {
          const found = this.findNodeAndAncestors(tree, currentCatId);
          if (found) {
            this.selectedCategoryName.set(found.target.name);
            this.expandedNodeIds.update((prev) => {
              const next = new Set(prev);
              found.ancestors.forEach((aId) => next.add(aId));
              return next;
            });
          }
        } else {
          // Expand first level by default for convenience
          const initialSet = new Set<string>();
          tree.forEach((root) => initialSet.add(root.id));
          this.expandedNodeIds.set(initialSet);
        }
      },
      error: () => {
        this.isLoadingCategories.set(false);
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

  private findNodeAndAncestors(
    nodes: CategoryTreeNode[],
    targetId: string,
    currentPath: string[] = []
  ): { target: CategoryTreeNode; ancestors: string[] } | null {
    for (const node of nodes) {
      if (node.id === targetId) {
        return { target: node, ancestors: currentPath };
      }
      if (node.children && node.children.length > 0) {
        const found = this.findNodeAndAncestors(node.children, targetId, [...currentPath, node.id]);
        if (found) return found;
      }
    }
    return null;
  }

  onSearchInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.categorySearchTerm.set(input.value);
  }

  toggleExpanded(nodeId: string): void {
    this.expandedNodeIds.update((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  }

  onNodeClick(item: FlattenedCategoryNode): void {
    if (!item.isLeaf) {
      // Parent node: toggle expand/collapse, cannot select
      this.toggleExpanded(item.node.id);
      return;
    }
    // Leaf node: select this leaf!
    this.form.controls.category_id.setValue(item.node.id);
    this.selectedCategoryName.set(item.node.name);
  }

  clearCategorySelection(): void {
    this.form.controls.category_id.setValue(null);
    this.selectedCategoryName.set(null);
  }

  onEngineChange(): void {
    const engine = this.form.controls.engine_type.value;
    if (engine && this.defaultPorts[engine]) {
      this.form.controls.port.setValue(this.defaultPorts[engine]);
    }
  }

  onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('modal-backdrop')) {
      this.cancel.emit();
    }
  }

  getCronHumanSummary(cron: string | null): string {
    if (!cron) return '';
    const trimmed = cron.trim();
    if (trimmed === '0 2 * * *') return 'هر شب ساعت ۰۲:۰۰ بامداد (Daily at 02:00 AM)';
    if (trimmed === '0 0 * * *') return 'هر روز ساعت ۱۲:۰۰ شب (Daily at midnight)';
    if (trimmed === '0 4 * * *') return 'هر شب ساعت ۰۴:۰۰ بامداد (Daily at 04:00 AM)';
    if (trimmed === '0 * * * *') return 'هر یک ساعت یک‌بار (Every hour)';
    if (trimmed === '*/30 * * * *') return 'هر ۳۰ دقیقه یک‌بار (Every 30 minutes)';
    if (trimmed === '*/15 * * * *') return 'هر ۱۵ دقیقه یک‌بار (Every 15 minutes)';
    const parts = trimmed.split(/\s+/);
    if (parts.length === 5) {
      const [m, h] = parts;
      if (!isNaN(Number(m)) && !isNaN(Number(h))) {
        return `هر روز ساعت ${h.padStart(2, '0')}:${m.padStart(2, '0')} (Daily at ${h}:${m})`;
      }
    }
    return 'برنامه همگام‌سازی زمان‌بندی شده (Custom Scheduled Sync)';
  }

  onSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    const val = this.form.getRawValue();
    const creds: Record<string, unknown> = {
      username: val.username,
    };
    if (val.password && val.password.trim()) {
      creds['password'] = val.password;
    }

    const request: ConnectionCreateRequest = {
      name: val.name,
      engine_type: val.engine_type,
      host: val.host,
      port: val.port,
      database_name: val.database_name,
      default_schema: val.default_schema?.trim() || undefined,
      owner: val.owner,
      category_id: val.category_id || undefined,
      credentials: creds,
      custom_params: {},
      cron_expression: val.cron_expression?.trim() || undefined,
    };

    this.save.emit(request);
  }
}

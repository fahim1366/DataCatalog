import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  HostListener,
  OnInit,
  computed,
  inject,
  signal,
  output,
  input,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { CommonModule } from "@angular/common";
import {
  AbstractControl,
  FormBuilder,
  ReactiveFormsModule,
  ValidationErrors,
  ValidatorFn,
  Validators,
} from "@angular/forms";
import { debounceTime, distinctUntilChanged, map, merge } from "rxjs";
import {
  ConnectionCreateRequest,
  ConnectionTestResult,
  DatabaseConnection,
  DatabaseEngine,
} from "./models/connection.models";
import { cronValidator } from "./validators/cron.validator";
import { ConnectionService } from "./services/connection.service";
import { CategoryTreeNode } from "../catalog/models/catalog.models";

export function jsonObjectValidator(): ValidatorFn {
  return (control: AbstractControl): ValidationErrors | null => {
    const val = control.value;
    if (!val || (typeof val === "string" && !val.trim())) {
      return null;
    }
    try {
      const parsed = JSON.parse(val);
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        Array.isArray(parsed)
      ) {
        return {
          invalidJson:
            'ورودی باید یک شیء معتبر JSON باشد (مانند: {"key": "value"})',
        };
      }
      return null;
    } catch {
      return { invalidJson: "خطا در ساختار JSON: فرمت وارد شده نامعتبر است" };
    }
  };
}

export interface FlattenedCategoryNode {
  node: CategoryTreeNode;
  level: number;
  isLeaf: boolean;
  parentPath: string;
}

@Component({
  selector: "app-connection-dialog",
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  template: `
    <div class="modal-backdrop" (click)="onBackdropClick($event)">
      <div
        class="modal-card"
        role="dialog"
        aria-labelledby="dialog-title"
        aria-modal="true"
      >
        <header class="modal-header">
          <h2 id="dialog-title">
            <i
              class="pi"
              [ngClass]="connectionToEdit() ? 'pi-pencil' : 'pi-plus-circle'"
            ></i>
            <span>{{
              connectionToEdit() ? "ویرایش پایگاه‌داده" : "ثبت پایگاه‌داده جدید"
            }}</span>
          </h2>
          <button
            type="button"
            class="btn-close"
            (click)="cancel.emit()"
            aria-label="بستن پنجره"
            title="بستن"
          >
            <i class="pi pi-times"></i>
          </button>
        </header>

        <form
          [formGroup]="form"
          (ngSubmit)="onSubmit()"
          class="connection-form"
        >
          <!-- Row 1 (Full Width): Database Title / Name -->
          <div class="form-row">
            <div class="form-group col-span-full">
              <label for="name"
                >عنوان پایگاه‌داده <span class="required">*</span></label
              >
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

          <!-- Row 2 (Full Width - Clean Card): Topic / Category Tree -->
          <div class="form-row">
            <div class="form-group col-span-full topic-section">
              <div class="topic-section-header">
                <div class="topic-header-title">
                  <label class="topic-label">
                    <i class="pi pi-sitemap topic-title-icon"></i>
                    <span>دسته‌بندی موضوعی در کاتالوگ</span>
                  </label>
                  <span class="optional-badge">اختیاری</span>
                </div>

                @if (selectedCategoryName()) {
                  <div class="selected-topic-chip">
                    <i class="pi pi-tag chip-leaf-icon"></i>
                    <span class="chip-text"
                      >موضوع:
                      <strong>{{ selectedCategoryName() }}</strong></span
                    >
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
                      placeholder="جستجوی شاخه موضوعی..."
                      [value]="categorySearchTerm()"
                      (input)="onSearchInput($event)"
                    />
                  </div>
                  @if (isLoadingCategories()) {
                    <span class="loading-hint">
                      <i class="pi pi-spin pi-spinner"></i>
                      <span>در حال بارگذاری موضوعات...</span>
                    </span>
                  }
                </div>

                <div class="topic-tree-viewport">
                  @if (
                    flattenedCategoryList().length === 0 &&
                    !isLoadingCategories()
                  ) {
                    <div class="empty-tree-hint">
                      هیچ موضوع یا برگی یافت نشد.
                    </div>
                  }

                  @for (item of flattenedCategoryList(); track item.node.id) {
                    <div
                      class="tree-node-row"
                      [class.is-leaf]="item.isLeaf"
                      [class.is-parent]="!item.isLeaf"
                      [class.is-selected]="
                        form.controls.category_id.value === item.node.id
                      "
                      [style.padding-right.px]="12 + item.level * 22"
                      (click)="onNodeClick(item)"
                    >
                      @if (!item.isLeaf) {
                        <!-- Parent Node (Branch / Folder) -->
                        <button
                          type="button"
                          class="btn-node-toggle"
                          (click)="
                            toggleExpanded(item.node.id);
                            $event.stopPropagation()
                          "
                          [attr.aria-label]="
                            expandedNodeIds().has(item.node.id)
                              ? 'بستن زیرشاخه'
                              : 'باز کردن زیرشاخه'
                          "
                        >
                          <i
                            class="pi"
                            [ngClass]="
                              expandedNodeIds().has(item.node.id)
                                ? 'pi-chevron-down'
                                : 'pi-chevron-left'
                            "
                          ></i>
                        </button>
                        <i
                          class="pi node-icon folder-icon"
                          [ngClass]="
                            expandedNodeIds().has(item.node.id)
                              ? 'pi-folder-open'
                              : 'pi-folder'
                          "
                        ></i>
                        <span class="node-name parent-name">{{
                          item.node.name
                        }}</span>
                      } @else {
                        <!-- Leaf Node (Selectable) -->
                        <span
                          class="leaf-radio-circle"
                          [class.checked]="
                            form.controls.category_id.value === item.node.id
                          "
                        ></span>
                        <i class="pi pi-file node-icon leaf-icon"></i>
                        <span class="node-name leaf-name">{{
                          item.node.name
                        }}</span>

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

          <!-- Row 3 (3 Columns: 1.2fr 1.8fr 0.8fr): Engine Type, Host / IP, Port -->
          <div class="form-row row-engine-host-port">
            <div class="form-group">
              <label for="engine_type"
                >نوع دیتابیس <span class="required">*</span></label
              >
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

            <div class="form-group">
              <label for="host"
                >آدرس سرور <span class="required">*</span></label
              >
              <input
                id="host"
                type="text"
                class="form-control font-mono ltr-input"
                dir="ltr"
                formControlName="host"
                placeholder="hostname or IP"
              />
              @if (form.controls.host.touched && form.controls.host.invalid) {
                <div class="field-error">آدرس سرور الزامی است.</div>
              }
            </div>

            <div class="form-group">
              <label for="port">پورت <span class="required">*</span></label>
              <input
                id="port"
                type="number"
                class="form-control font-mono ltr-input"
                dir="ltr"
                formControlName="port"
              />
              @if (form.controls.port.touched && form.controls.port.invalid) {
                <div class="field-error">
                  پورت معتبر (۱ تا ۶۵۵۳۵) الزامی است.
                </div>
              }
            </div>
          </div>

          <!-- Contextual Dialect Helper Hint -->
          @if (engineHint()) {
            <div class="form-row">
              <div class="form-group col-span-full">
                <div class="dialect-hint-card">
                  <i class="pi pi-info-circle dialect-hint-icon"></i>
                  <span class="dialect-hint-text">{{ engineHint() }}</span>
                </div>
              </div>
            </div>
          }

          <!-- Row 4 (2 Equal Columns: 1fr 1fr): Username (Right) & Password (Left) -->
          <div class="form-row row-credentials">
            <div class="form-group">
              <label for="username"
                >نام کاربری اتصال <span class="required">*</span></label
              >
              <input
                id="username"
                type="text"
                class="form-control font-mono ltr-input"
                dir="ltr"
                formControlName="username"
                autocomplete="off"
                placeholder="username"
              />
              @if (
                form.controls.username.touched && form.controls.username.invalid
              ) {
                <div class="field-error">نام کاربری الزامی است.</div>
              }
            </div>

            <div class="form-group">
              <label for="password">
                {{
                  connectionToEdit()
                    ? "رمز عبور جدید (اختیاری در ویرایش)"
                    : "رمز عبور"
                }}
                @if (!connectionToEdit()) {
                  <span class="required">*</span>
                }
              </label>
              <input
                id="password"
                type="password"
                class="form-control font-mono ltr-input"
                dir="ltr"
                formControlName="password"
                autocomplete="new-password"
                [placeholder]="
                  connectionToEdit()
                    ? 'در صورت عدم تغییر، خالی بگذارید'
                    : 'رمز عبور امن دیتابیس'
                "
              />
              @if (
                form.controls.password.touched && form.controls.password.invalid
              ) {
                <div class="field-error">رمز عبور الزامی است.</div>
              }
            </div>
          </div>

          <!-- Row 5 (2 Columns: 1.5fr 1fr): Target Database Combobox & Default Schema -->
          <div class="form-row row-db-schema">
            <div class="form-group">
              <div class="field-label-row">
                <label for="database_name"
                  >نام دیتابیس <span class="required">*</span></label
                >
              </div>

              <div class="combobox-container" dir="ltr">
                <div class="combobox-input-group">
                  <input
                    id="database_name"
                    type="text"
                    class="form-control font-mono ltr-input combobox-input"
                    dir="ltr"
                    formControlName="database_name"
                    placeholder="target_db_name"
                    autocomplete="off"
                    (focus)="onComboboxFocus()"
                  />

                  @if (discoveredDatabases().length > 0) {
                    <button
                      type="button"
                      class="btn-combobox-toggle"
                      (click)="toggleDropdown()"
                      [attr.aria-expanded]="isDropdownOpen()"
                      title="نمایش یا بستن لیست دیتابیس‌های کشف‌شده"
                    >
                      <i
                        class="pi"
                        [ngClass]="
                          isDropdownOpen() ? 'pi-chevron-up' : 'pi-chevron-down'
                        "
                      ></i>
                    </button>
                  }

                  <button
                    type="button"
                    class="btn-discover"
                    (click)="discoverDatabases()"
                    [disabled]="
                      isDiscoveringDatabases() || !canDiscoverDatabases()
                    "
                    title="شناسایی خودکار پایگاه‌های داده بر اساس مشخصات هاست و دسترسی (Auto-Discover)"
                  >
                    <i
                      class="pi"
                      [ngClass]="
                        isDiscoveringDatabases()
                          ? 'pi-spin pi-spinner'
                          : 'pi-sync'
                      "
                    ></i>
                    <span>{{
                      isDiscoveringDatabases() ? "در حال شناسایی..." : "شناسایی"
                    }}</span>
                  </button>
                </div>

                <!-- Custom Floating Combobox Dropdown -->
                @if (isDropdownOpen() && discoveredDatabases().length > 0) {
                  <div
                    class="combobox-dropdown"
                    (mousedown)="$event.preventDefault()"
                  >
                    <div class="dropdown-header">
                      پایگاه‌های داده شناسایی‌شده:
                    </div>
                    <div class="dropdown-list">
                      @for (db of discoveredDatabases(); track db) {
                        <button
                          type="button"
                          class="dropdown-item"
                          [class.active]="
                            form.controls.database_name.value === db
                          "
                          (click)="selectDiscoveredDatabase(db)"
                        >
                          <i class="pi pi-database item-icon"></i>
                          <span class="item-name font-mono">{{ db }}</span>
                          @if (form.controls.database_name.value === db) {
                            <i class="pi pi-check selected-check"></i>
                          }
                        </button>
                      }
                    </div>
                  </div>
                }
              </div>

              <!-- Auto-Discovery Status Feedback -->
              @if (discoveryStatus() === "success") {
                <div class="discovery-status success animate-fade">
                  <i class="pi pi-check-circle"></i>
                  <span>{{ discoveryMessage() }}</span>
                </div>
              } @else if (discoveryStatus() === "error") {
                <div class="discovery-status error animate-fade">
                  <i class="pi pi-exclamation-circle"></i>
                  <span>{{ discoveryMessage() }}</span>
                </div>
              } @else if (discoveryStatus() === "loading") {
                <div class="discovery-status loading animate-fade">
                  <i class="pi pi-spin pi-spinner"></i>
                  <span>در حال شناسایی پایگاه‌های داده...</span>
                </div>
              }

              @if (
                form.controls.database_name.touched &&
                form.controls.database_name.invalid
              ) {
                <div class="field-error">نام پایگاه‌داده الزامی است.</div>
              }
            </div>

            <div class="form-group">
              <label for="default_schema">اسکیمای پیش‌فرض</label>
              <input
                id="default_schema"
                type="text"
                class="form-control font-mono ltr-input"
                dir="ltr"
                formControlName="default_schema"
                placeholder="public"
              />
            </div>
          </div>

          <!-- Row 6 (2 Columns: 1fr 1.3fr): Owner & Cron Schedule -->
          <div class="form-row row-owner-cron">
            <div class="form-group">
              <label for="owner">مالک / تیم مسئول</label>
              <input
                id="owner"
                type="text"
                class="form-control"
                formControlName="owner"
                placeholder="مثال: data-platform یا bi-team"
              />
            </div>

            <div class="form-group">
              <label for="cron_expression">زمان‌بندی همگام‌سازی</label>
              <input
                id="cron_expression"
                type="text"
                class="form-control font-mono ltr-input"
                dir="ltr"
                formControlName="cron_expression"
                placeholder="0 2 * * * (هر شب ساعت ۲ بامداد)"
              />
              <span class="cron-hint"
                >الگوی استاندارد کرون ۵ بخشی: &lt;دقیقه&gt; &lt;ساعت&gt;
                &lt;روز&gt; &lt;ماه&gt; &lt;روز هفته&gt;</span
              >

              @if (form.controls.cron_expression.errors?.["invalidCron"]) {
                <div class="cron-error-feedback">
                  <i class="pi pi-times-circle"></i>
                  <span>{{
                    form.controls.cron_expression.errors?.["invalidCron"]
                      ?.message
                  }}</span>
                </div>
              } @else if (
                form.controls.cron_expression.value &&
                form.controls.cron_expression.valid
              ) {
                <div class="cron-valid-feedback">
                  <div class="cron-valid-line">
                    <i class="pi pi-check-circle"></i>
                    <span>الگوی معتبر زمان‌بندی</span>
                  </div>
                  <div class="cron-human-summary">
                    {{
                      getCronHumanSummary(form.controls.cron_expression.value)
                    }}
                  </div>
                </div>
              }
            </div>
          </div>

          <!-- Row 7 (Full Width - Collapsible): Custom Params JSONB -->
          <div class="form-row">
            <div class="form-group col-span-full custom-params-container">
              <button
                type="button"
                class="btn-toggle-custom-params"
                (click)="isCustomParamsOpen.update((v) => !v)"
                [attr.aria-expanded]="isCustomParamsOpen()"
              >
                <div class="toggle-title-group">
                  <i
                    class="pi"
                    [ngClass]="
                      isCustomParamsOpen()
                        ? 'pi-chevron-down'
                        : 'pi-chevron-left'
                    "
                  ></i>
                  <i class="pi pi-cog"></i>
                  <span class="custom-params-title">تنظیمات اضافی اتصال</span>
                  <span class="optional-badge">اختیاری</span>
                </div>
                <span class="toggle-hint-text"
                  >پارامترهای پیشرفته درایور مانند SSL، timeout و pool
                  settings</span
                >
              </button>

              @if (isCustomParamsOpen()) {
                <div class="custom-params-content animate-fade">
                  <label for="custom_params_json" class="custom-params-label">
                    <span>پیکربندی JSON پارامترهای سفارشی:</span>
                  </label>
                  <textarea
                    id="custom_params_json"
                    class="form-control font-mono custom-params-textarea ltr-input"
                    formControlName="custom_params_json"
                    rows="4"
                    dir="ltr"
                    placeholder='{ "sslmode": "prefer", "connect_timeout": 10 }'
                  ></textarea>

                  <!-- Contextual Dialect Quick Parameters -->
                  <div class="dialect-quick-params">
                    <span class="quick-params-label">الگوهای سریع بر اساس موتور انتخابی:</span>
                    <div class="quick-chips">
                      @if (selectedEngine() === 'ORACLE') {
                        <button type="button" class="btn-quick-chip" (click)="applyQuickParam('{ &quot;service_name&quot;: &quot;ORCLPDB1&quot; }')">Service Name: ORCLPDB1</button>
                        <button type="button" class="btn-quick-chip" (click)="applyQuickParam('{ &quot;sid&quot;: &quot;ORCL&quot; }')">SID: ORCL</button>
                      } @else if (selectedEngine() === 'MSSQL') {
                        <button type="button" class="btn-quick-chip" (click)="applyQuickParam('{ &quot;driver&quot;: &quot;ODBC Driver 18 for SQL Server&quot;, &quot;TrustServerCertificate&quot;: &quot;yes&quot; }')">Driver &amp; TrustServerCertificate</button>
                      } @else if (selectedEngine() === 'MONGODB') {
                        <button type="button" class="btn-quick-chip" (click)="applyQuickParam('{ &quot;authSource&quot;: &quot;admin&quot; }')">Auth Source: admin</button>
                      } @else if (selectedEngine() === 'MYSQL') {
                        <button type="button" class="btn-quick-chip" (click)="applyQuickParam('{ &quot;charset&quot;: &quot;utf8mb4&quot; }')">Charset: utf8mb4</button>
                      } @else if (selectedEngine() === 'POSTGRESQL') {
                        <button type="button" class="btn-quick-chip" (click)="applyQuickParam('{ &quot;sslmode&quot;: &quot;prefer&quot; }')">SSL Mode: prefer</button>
                      }
                    </div>
                  </div>

                  @if (
                    form.controls.custom_params_json.touched &&
                    form.controls.custom_params_json.errors?.["invalidJson"]
                  ) {
                    <div class="custom-params-error-feedback">
                      <i class="pi pi-times-circle"></i>
                      <span>{{
                        form.controls.custom_params_json.errors?.["invalidJson"]
                      }}</span>
                    </div>
                  } @else {
                    <span class="custom-params-hint">
                      فرمت JSON معتبر وارد نمایید. پیش‌فرض:
                      <code>&#123;&#125;</code>
                    </span>
                  }
                </div>
              }
            </div>
          </div>

          @if (testResult()) {
            <div
              class="test-result-banner animate-fade"
              [class.test-success]="testResult()!.success"
              [class.test-failure]="!testResult()!.success"
            >
              <div class="test-result-header">
                <i
                  class="pi"
                  [ngClass]="
                    testResult()!.success
                      ? 'pi-check-circle'
                      : 'pi-times-circle'
                  "
                ></i>
                <strong>{{
                  testResult()!.success
                    ? "اتصال به پایگاه‌داده با موفقیت برقرار شد."
                    : "خطا در برقراری اتصال"
                }}</strong>
                @if (testResult()!.latency_ms) {
                  <span class="latency-tag mono-font"
                    >{{ testResult()!.latency_ms }}ms</span
                  >
                }
              </div>
              <div class="test-result-message">{{ testResult()!.message }}</div>
            </div>
          }

          @if (errorMessage()) {
            <div class="alert-error">
              <i class="pi pi-exclamation-triangle"></i>
              <span>{{ errorMessage() }}</span>
            </div>
          }

          <!-- Footer Button Hierarchy: Right=Cancel, Left=Actions (Outline Teal + Solid Primary) -->
          <footer class="modal-footer">
            <button type="button" class="btn-cancel" (click)="cancel.emit()">
              انصراف
            </button>

            <div class="footer-actions">
              <button
                type="button"
                class="btn-test-conn btn-with-icon"
                (click)="testUnsavedConnection()"
                [disabled]="isTestingConnection() || !canTestConnection()"
                title="تست اتصال زنده قبل از ثبت در سامانه"
              >
                @if (isTestingConnection()) {
                  <i class="pi pi-spin pi-spinner"></i>
                  <span>در حال تست اتصال...</span>
                } @else {
                  <i class="pi pi-bolt"></i>
                  <span>تست اتصال</span>
                }
              </button>

              <button
                type="submit"
                class="btn-primary btn-with-icon"
                [disabled]="form.invalid || isSubmitting()"
              >
                @if (isSubmitting()) {
                  <i class="pi pi-spin pi-spinner"></i>
                  <span>در حال ذخیره و اتصال...</span>
                } @else if (connectionToEdit()) {
                  <i class="pi pi-check"></i>
                  <span>ذخیره تغییرات پایگاه‌داده</span>
                } @else {
                  <i class="pi pi-check"></i>
                  <span>ثبت پایگاه‌داده</span>
                }
              </button>
            </div>
          </footer>
        </form>
      </div>
    </div>
  `,
  styles: [
    `
      .modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(15, 23, 42, 0.6);
        backdrop-filter: blur(4px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1000;
        padding: 1.25rem;
        direction: rtl;
        font-family: var(--font-family-sans, "Vazirmatn", sans-serif);
      }
      .modal-card {
        background: #ffffff;
        border-radius: 12px;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
        width: 100%;
        max-width: 780px;
        max-height: 90vh;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
      }
      @media (max-width: 768px) {
        .modal-backdrop {
          padding: 12px;
        }
        .modal-card {
          max-width: calc(100vw - 24px);
          max-height: 85vh;
        }
      }
      .modal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1.25rem 1.75rem;
        border-bottom: 1px solid #e2e8f0;
        background: #ffffff;
      }
      .modal-header h2 {
        display: flex;
        align-items: center;
        gap: 0.6rem;
        margin: 0;
        font-size: 1.15rem;
        font-weight: 700;
        color: #0f172a;
      }
      .modal-header h2 .pi {
        color: #2563eb;
        font-size: 1.1rem;
      }
      .btn-close {
        background: none;
        border: 1px solid #cbd5e1;
        border-radius: 50%;
        font-size: 0.8rem;
        cursor: pointer;
        color: #64748b;
        width: 32px;
        height: 32px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.15s ease;
      }
      .btn-close:hover {
        background: #f1f5f9;
        color: #0f172a;
        border-color: #94a3b8;
      }
      .connection-form {
        padding: 1.5rem 1.75rem;
        display: flex;
        flex-direction: column;
        gap: 1.25rem;
      }
      .form-row {
        display: grid;
        gap: 1.15rem;
      }
      .col-span-full {
        grid-column: 1 / -1;
      }
      .row-engine-host-port {
        grid-template-columns: 1.2fr 1.8fr 0.8fr;
      }
      .row-credentials {
        direction: rtl;
        grid-template-columns: 1fr 1fr;
      }
      .row-db-schema {
        grid-template-columns: 1.5fr 1fr;
      }
      .row-owner-cron {
        grid-template-columns: 1fr 1.3fr;
      }
      .form-group {
        display: flex;
        flex-direction: column;
        gap: 0.4rem;
      }
      .form-group label {
        font-size: 0.82rem;
        font-weight: 600;
        color: #334155;
      }
      .field-label-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .required {
        color: #ef4444;
        margin-right: 2px;
      }
      .form-control {
        padding: 0.55rem 0.85rem;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        font-size: 0.85rem;
        color: #0f172a;
        background: #ffffff;
        outline: none;
        transition:
          border-color 0.15s,
          box-shadow 0.15s;
        width: 100%;
        box-sizing: border-box;
      }
      .form-control:focus {
        border-color: #2563eb;
        box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.12);
      }
      .font-mono {
        font-family: var(--font-family-mono, monospace);
      }
      .ltr-input {
        direction: ltr;
        text-align: left;
      }
      .field-error {
        color: #ef4444;
        font-size: 0.75rem;
        margin-top: 0.15rem;
      }

      /* Combobox Component Styles */
      .combobox-container {
        position: relative;
        width: 100%;
      }
      .combobox-input-group {
        display: flex;
        align-items: stretch;
        width: 100%;
        position: relative;
      }
      .combobox-input {
        flex: 1 1 auto;
        min-width: 0;
        border-top-right-radius: 0;
        border-bottom-right-radius: 0;
        z-index: 0;
      }
      .combobox-input:focus {
        z-index: 2;
      }
      .btn-combobox-toggle {
        display: flex;
        align-items: center;
        justify-content: center;
        background: #f8fafc;
        border: 1px solid #cbd5e1;
        border-left: none;
        border-right: none;
        color: #64748b;
        padding: 0 0.55rem;
        cursor: pointer;
        font-size: 0.75rem;
        transition:
          background-color 0.15s ease,
          color 0.15s ease;
        flex-shrink: 0;
        z-index: 1;
      }
      .btn-combobox-toggle:hover {
        background: #f1f5f9;
        color: #0f172a;
      }
      .btn-discover {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 0.35rem;
        background: #eff6ff;
        border: 1px solid #bfdbfe;
        border-left: none;
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
        border-top-left-radius: 0;
        border-bottom-left-radius: 0;
        color: #1d4ed8;
        font-size: 0.8rem;
        font-weight: 600;
        padding: 0.5rem 0.85rem;
        white-space: nowrap;
        flex-shrink: 0;
        cursor: pointer;
        font-family: inherit;
        transition: all 0.15s ease;
        z-index: 1;
      }
      .btn-discover:hover:not(:disabled) {
        background: #dbeafe;
        border-color: #93c5fd;
        color: #1e40af;
      }
      .btn-discover:disabled {
        opacity: 0.55;
        cursor: not-allowed;
        background: #f8fafc;
        color: #94a3b8;
        border-color: #e2e8f0;
      }
      .combobox-dropdown {
        position: absolute;
        top: calc(100% + 4px);
        left: 0;
        right: 0;
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        box-shadow:
          0 10px 15px -3px rgba(0, 0, 0, 0.1),
          0 4px 6px -4px rgba(0, 0, 0, 0.1);
        z-index: 50;
        max-height: 200px;
        overflow-y: auto;
        direction: ltr;
      }
      .dropdown-header {
        padding: 6px 12px;
        font-size: 0.72rem;
        font-weight: 600;
        color: #64748b;
        background: #f8fafc;
        border-bottom: 1px solid #e2e8f0;
        text-align: right;
        direction: rtl;
      }
      .dropdown-list {
        display: flex;
        flex-direction: column;
      }
      .dropdown-item {
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        padding: 8px 12px;
        background: none;
        border: none;
        text-align: left;
        cursor: pointer;
        font-size: 0.82rem;
        color: #1e293b;
        transition: background-color 0.15s;
      }
      .dropdown-item:hover {
        background: #f0fdf4;
        color: #166534;
      }
      .dropdown-item.active {
        background: #eff6ff;
        color: #1d4ed8;
        font-weight: 600;
      }
      .dropdown-item .item-icon {
        font-size: 0.8rem;
        color: #3b82f6;
      }
      .dropdown-item .item-name {
        flex: 1;
      }
      .dropdown-item .selected-check {
        color: #2563eb;
        font-size: 0.75rem;
      }
      .discovery-status {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.75rem;
        font-weight: 500;
        margin-top: 4px;
      }
      .discovery-status.success {
        color: #059669;
      }
      .discovery-status.error {
        color: #dc2626;
      }
      .discovery-status.loading {
        color: #2563eb;
      }

      /* Topic Section & Tree Picker */
      .topic-section {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 0.85rem 1rem;
      }
      .topic-section-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.65rem;
        flex-wrap: wrap;
        gap: 0.5rem;
      }
      .topic-header-title {
        display: flex;
        align-items: center;
        gap: 0.5rem;
      }
      .topic-label {
        display: flex;
        align-items: center;
        gap: 0.45rem;
        font-size: 0.85rem;
        font-weight: 700;
        color: #1e293b;
        margin: 0;
      }
      .topic-title-icon {
        color: #2563eb;
        font-size: 0.9rem;
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
        padding: 0.45rem 0.75rem;
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
        max-height: 160px;
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

      /* Cron Hint & Validation */
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

      /* Custom Params Section */
      .custom-params-container {
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        overflow: hidden;
        background: #f8fafc;
        transition: all 0.2s ease;
      }
      .btn-toggle-custom-params {
        width: 100%;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 14px;
        background: transparent;
        border: none;
        cursor: pointer;
        text-align: right;
        color: #1e293b;
        font-family: inherit;
        transition: background-color 0.15s ease;
      }
      .btn-toggle-custom-params:hover {
        background-color: #f1f5f9;
      }
      .toggle-title-group {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .custom-params-title {
        font-size: 0.85rem;
        font-weight: 700;
        color: #334155;
      }
      .optional-badge {
        font-size: 0.7rem;
        background: #e2e8f0;
        color: #64748b;
        padding: 2px 6px;
        border-radius: 4px;
      }
      .toggle-hint-text {
        font-size: 0.75rem;
        color: #94a3b8;
      }
      .custom-params-content {
        padding: 12px 14px;
        border-top: 1px solid #e2e8f0;
        background: #ffffff;
      }
      .custom-params-label {
        display: block;
        font-size: 0.8rem;
        font-weight: 600;
        color: #475569;
        margin-bottom: 6px;
      }
      .custom-params-textarea {
        resize: vertical;
        min-height: 80px;
        font-size: 0.82rem;
        line-height: 1.5;
      }
      .custom-params-error-feedback {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 6px;
        font-size: 0.78rem;
        color: #dc2626;
        font-weight: 500;
      }
      .custom-params-hint {
        display: block;
        margin-top: 6px;
        font-size: 0.75rem;
        color: #64748b;
      }

      /* Test Result & Errors */
      .test-result-banner {
        padding: 10px 14px;
        border-radius: 6px;
        margin-bottom: 12px;
        font-size: 0.82rem;
        line-height: 1.5;
      }
      .test-result-banner.test-success {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        color: #166534;
      }
      .test-result-banner.test-failure {
        background: #fef2f2;
        border: 1px solid #fecaca;
        color: #991b1b;
      }
      .test-result-header {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.85rem;
        margin-bottom: 4px;
      }
      .test-result-message {
        font-size: 0.8rem;
        opacity: 0.95;
      }
      .latency-tag {
        background: #e2e8f0;
        color: #334155;
        font-size: 0.72rem;
        font-weight: 700;
        padding: 1px 6px;
        border-radius: 4px;
        margin-right: auto;
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

      /* Footer Button Hierarchy: Right=Cancel, Left=Actions */
      .modal-footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-top: 1.25rem;
        border-top: 1px solid #e2e8f0;
        margin-top: 0.5rem;
      }
      .btn-with-icon {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
      }
      .btn-cancel {
        padding: 0.55rem 1.25rem;
        background: transparent;
        color: #64748b;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        cursor: pointer;
        font-weight: 500;
        font-size: 0.85rem;
        font-family: inherit;
        transition: all 0.15s ease;
      }
      .btn-cancel:hover {
        background: #f1f5f9;
        color: #1e293b;
        border-color: #94a3b8;
      }
      .footer-actions {
        display: flex;
        align-items: center;
        gap: 0.75rem;
      }
      .btn-test-conn {
        padding: 0.55rem 1.15rem;
        background: #f0fdfa;
        color: #0f766e;
        border: 1.5px solid #0d9488;
        border-radius: 6px;
        cursor: pointer;
        font-weight: 600;
        font-size: 0.85rem;
        font-family: inherit;
        transition: all 0.15s ease;
      }
      .btn-test-conn:hover:not(:disabled) {
        background: #ccfbf1;
        color: #0f766e;
      }
      .btn-test-conn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
        border-color: #99f6e4;
      }
      .btn-primary {
        padding: 0.55rem 1.35rem;
        background: #2563eb;
        color: #ffffff;
        border: 1px solid #2563eb;
        border-radius: 6px;
        cursor: pointer;
        font-weight: 600;
        font-size: 0.85rem;
        font-family: inherit;
        transition: all 0.15s ease;
      }
      .btn-primary:hover:not(:disabled) {
        background: #1d4ed8;
        border-color: #1d4ed8;
      }
      .btn-primary:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .dialect-hint-card {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 6px;
        color: #166534;
        font-size: 12px;
        line-height: 1.4;
      }
      .dialect-hint-icon {
        color: #15803d;
        font-size: 14px;
        flex-shrink: 0;
      }
      .dialect-quick-params {
        margin-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .quick-params-label {
        font-size: 11px;
        color: #64748b;
      }
      .quick-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .btn-quick-chip {
        background: #e2e8f0;
        border: 1px solid #cbd5e1;
        border-radius: 4px;
        padding: 3px 8px;
        font-size: 11px;
        font-family: inherit;
        color: #334155;
        cursor: pointer;
        transition: all 0.2s ease;
      }
      .btn-quick-chip:hover {
        background: #cbd5e1;
        color: #0f172a;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionDialogComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly connectionService = inject(ConnectionService);
  private readonly destroyRef = inject(DestroyRef);

  readonly connectionToEdit = input<DatabaseConnection | null>(null);
  readonly preselectedCategoryId = input<string | null>(null);

  readonly save = output<ConnectionCreateRequest>();
  readonly cancel = output<void>();

  // Database Combobox & Discovery Signals
  readonly discoveredDatabases = signal<string[]>([]);
  readonly isDiscoveringDatabases = signal<boolean>(false);
  readonly discoveryStatus = signal<"idle" | "loading" | "success" | "error">(
    "idle",
  );
  readonly discoveryMessage = signal<string | null>(null);
  readonly isDropdownOpen = signal<boolean>(false);

  // Test Connection Signals
  readonly isTestingConnection = signal<boolean>(false);
  readonly testResult = signal<ConnectionTestResult | null>(null);

  readonly supportedEngines: DatabaseEngine[] = [
    "POSTGRESQL",
    "MSSQL",
    "ORACLE",
    "MYSQL",
    "CLICKHOUSE",
    "MONGODB",
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
  readonly isCustomParamsOpen = signal<boolean>(false);
  readonly selectedEngine = signal<DatabaseEngine>("POSTGRESQL");

  readonly engineHint = computed<string>(() => {
    const engine = this.selectedEngine();
    switch (engine) {
      case "POSTGRESQL":
        return "پورت استاندارد: ۵۴۳۲ | طرح‌واره پیش‌فرض: public | اتصال مقید به زمان با NullPool";
      case "MSSQL":
        return "پورت استاندارد: ۱۴۳۳ | درایور aioodbc/pymssql | پشتیبانی از پارامترهای Driver و TrustServerCertificate";
      case "MYSQL":
        return "پورت استاندارد: ۳۳۰۶ | شناسایی بر مبنای INFORMATION_SCHEMA | پشتیبانی از aiomysql/asyncmy";
      case "ORACLE":
        return "پورت استاندارد: ۱۵۲۱ | حالت اتصال: Service Name یا SID (در تنظیمات اضافی: service_name / sid)";
      case "CLICKHOUSE":
        return "پورت استاندارد HTTP: ۸۱۲۳ | ارتباط مستقیم بر بستر JSON HTTP بدون نیاز به درایور باینری";
      case "MONGODB":
        return "پورت استاندارد: ۲۷۰۱۷ | پشتیبانی از منبع اعتبارسنجی (Auth Source: admin) | نمونه‌برداری ۵۰ سند";
      default:
        return "";
    }
  });

  // Category Tree Signals
  readonly rawCategories = signal<CategoryTreeNode[]>([]);
  readonly isLoadingCategories = signal<boolean>(false);
  readonly categorySearchTerm = signal<string>("");
  readonly expandedNodeIds = signal<Set<string>>(new Set<string>());
  readonly selectedCategoryName = signal<string | null>(null);

  readonly form = this.fb.group({
    name: this.fb.control<string>("", {
      nonNullable: true,
      validators: [
        Validators.required,
        Validators.minLength(1),
        Validators.maxLength(200),
      ],
    }),
    category_id: this.fb.control<string | null>(null),
    engine_type: this.fb.control<DatabaseEngine>("POSTGRESQL", {
      nonNullable: true,
      validators: [Validators.required],
    }),
    host: this.fb.control<string>("localhost", {
      nonNullable: true,
      validators: [
        Validators.required,
        Validators.minLength(1),
        Validators.maxLength(255),
      ],
    }),
    port: this.fb.control<number>(5432, {
      nonNullable: true,
      validators: [
        Validators.required,
        Validators.min(1),
        Validators.max(65535),
      ],
    }),
    database_name: this.fb.control<string>("", {
      nonNullable: true,
      validators: [
        Validators.required,
        Validators.minLength(1),
        Validators.maxLength(255),
      ],
    }),
    default_schema: this.fb.control<string | null>("public"),
    owner: this.fb.control<string>("admin", {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(255)],
    }),
    username: this.fb.control<string>("", {
      nonNullable: true,
      validators: [Validators.required],
    }),
    password: this.fb.control<string>("", {
      nonNullable: true,
      validators: [Validators.required],
    }),
    cron_expression: this.fb.control<string | null>("", {
      validators: [cronValidator()],
    }),
    custom_params_json: this.fb.control<string>("{}", {
      nonNullable: true,
      validators: [jsonObjectValidator()],
    }),
  });

  // Flattened and filtered category list for display in the tree picker
  readonly flattenedCategoryList = computed<FlattenedCategoryNode[]>(() => {
    const raw = this.rawCategories();
    const expanded = this.expandedNodeIds();
    const term = this.categorySearchTerm().trim().toLowerCase();

    const result: FlattenedCategoryNode[] = [];

    const traverse = (
      nodes: CategoryTreeNode[],
      level: number,
      parentPath: string,
    ) => {
      for (const node of nodes) {
        const isLeaf = !node.children || node.children.length === 0;
        const currentPath = parentPath
          ? `${parentPath} > ${node.name}`
          : node.name;

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

    traverse(raw, 0, "");
    return result;
  });

  ngOnInit(): void {
    this.loadCategoryTree();

    // Auto-update port when engine_type changes
    this.form.controls.engine_type.valueChanges
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((engine) => {
        if (engine) {
          this.selectedEngine.set(engine);
          if (this.defaultPorts[engine]) {
            this.form.controls.port.setValue(this.defaultPorts[engine]);
          }
        }
      });

    // Auto-discover databases when host, port, username, password are all filled (debounced 600ms)
    merge(
      this.form.controls.engine_type.valueChanges,
      this.form.controls.host.valueChanges,
      this.form.controls.port.valueChanges,
      this.form.controls.username.valueChanges,
      this.form.controls.password.valueChanges,
    )
      .pipe(
        debounceTime(600),
        map(() => {
          const val = this.form.getRawValue();
          return `${val.engine_type}|${val.host}|${val.port}|${val.username}|${val.password}`;
        }),
        distinctUntilChanged(),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        if (this.canDiscoverDatabases() && !this.isDiscoveringDatabases()) {
          this.discoverDatabases();
        }
      });

    const connectionToEdit = this.connectionToEdit();
    const preselectedCategoryId = this.preselectedCategoryId();
    if (connectionToEdit) {
      const conn = connectionToEdit;
      const customParamsVal =
        conn.custom_params && Object.keys(conn.custom_params).length > 0
          ? JSON.stringify(conn.custom_params, null, 2)
          : "{}";
      this.form.patchValue({
        name: conn.name,
        category_id: conn.category_id || null,
        engine_type: conn.engine_type,
        host: conn.host,
        port: conn.port,
        database_name: conn.database_name,
        default_schema: conn.default_schema || "",
        owner: conn.owner,
        username: conn.masked_credentials?.["username"] || "admin",
        password: "",
        cron_expression: conn.sync_schedule?.cron_expression || "",
        custom_params_json: customParamsVal,
      });
      if (conn.category_name) {
        this.selectedCategoryName.set(conn.category_name);
      }
      if (conn.custom_params && Object.keys(conn.custom_params).length > 0) {
        this.isCustomParamsOpen.set(true);
      }
      // In edit mode, password is optional
      this.form.controls.password.clearValidators();
      this.form.controls.password.updateValueAndValidity();
    } else if (preselectedCategoryId) {
      this.form.controls.category_id.setValue(preselectedCategoryId);
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
      if (
        child.name.toLowerCase().includes(term) ||
        this.hasMatchingDescendant(child, term)
      ) {
        return true;
      }
    }
    return false;
  }

  private findNodeAndAncestors(
    nodes: CategoryTreeNode[],
    targetId: string,
    currentPath: string[] = [],
  ): { target: CategoryTreeNode; ancestors: string[] } | null {
    for (const node of nodes) {
      if (node.id === targetId) {
        return { target: node, ancestors: currentPath };
      }
      if (node.children && node.children.length > 0) {
        const found = this.findNodeAndAncestors(node.children, targetId, [
          ...currentPath,
          node.id,
        ]);
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
    if (engine) {
      this.selectedEngine.set(engine);
      if (this.defaultPorts[engine]) {
        this.form.controls.port.setValue(this.defaultPorts[engine]);
      }
    }
  }

  applyQuickParam(paramJson: string): void {
    this.form.controls.custom_params_json.setValue(paramJson);
    this.form.controls.custom_params_json.markAsDirty();
  }

  onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains("modal-backdrop")) {
      this.cancel.emit();
    }
  }

  getCronHumanSummary(cron: string | null): string {
    if (!cron) return "";
    const trimmed = cron.trim();
    if (trimmed === "0 2 * * *") return "هر شب ساعت ۰۲:۰۰ بامداد";
    if (trimmed === "0 0 * * *") return "هر روز ساعت ۱۲:۰۰ شب";
    if (trimmed === "0 4 * * *") return "هر شب ساعت ۰۴:۰۰ بامداد";
    if (trimmed === "0 * * * *") return "هر یک ساعت یک‌بار";
    if (trimmed === "*/30 * * * *") return "هر ۳۰ دقیقه یک‌بار";
    if (trimmed === "*/15 * * * *") return "هر ۱۵ دقیقه یک‌بار";
    const parts = trimmed.split(/\s+/);
    if (parts.length === 5) {
      const [m, h] = parts;
      if (!isNaN(Number(m)) && !isNaN(Number(h))) {
        return `هر روز ساعت ${h.padStart(2, "0")}:${m.padStart(2, "0")}`;
      }
    }
    return "برنامه همگام‌سازی زمان‌بندی شده";
  }

  translateErrorMessage(
    error: string | null | undefined,
    context: "discovery" | "test",
  ): string {
    if (!error || !error.trim()) {
      return context === "discovery"
        ? "خطا در شناسایی پایگاه‌های داده: امکان اتصال به سرور وجود ندارد. لطفاً آدرس سرور، پورت و مشخصات کاربری را بررسی کرده یا از دکمه «تست اتصال» در پایین فرم استفاده نمایید."
        : "خطا در برقراری اتصال به پایگاه‌داده. لطفاً آدرس سرور، پورت و مشخصات کاربری را بررسی کرده و مجدداً از دکمه «تست اتصال» استفاده کنید.";
    }

    const err = error.trim().toLowerCase();

    // Timeout
    if (
      err.includes("timeout") ||
      err.includes("timed out") ||
      err.includes("time out")
    ) {
      return context === "discovery"
        ? "مهلت زمانی شناسایی پایگاه‌های داده به پایان رسید: سرور در مدت ۵ ثانیه پاسخ نداد. لطفاً آدرس سرور (هاست) و پورت را بررسی کرده یا از دکمه «تست اتصال» در پایین فرم استفاده کنید."
        : "مهلت زمانی اتصال به پایان رسید: سرور در مدت ۵ ثانیه پاسخ نداد. لطفاً آدرس سرور (هاست)، پورت و وضعیت ارتباط شبکه را بررسی نموده یا مجدداً از «تست اتصال» استفاده کنید.";
    }

    // Credentials / Authentication
    if (
      err.includes("password authentication failed") ||
      err.includes("authentication failed") ||
      err.includes("access denied") ||
      err.includes("credential") ||
      err.includes("auth_valid") ||
      err.includes("invalid username or password") ||
      err.includes("wrong_password") ||
      err.includes("login failed")
    ) {
      return context === "discovery"
        ? "خطا در احراز هویت: نام کاربری یا رمز عبور نامعتبر است. لطفاً مشخصات کاربری را بررسی کرده یا از دکمه «تست اتصال» در پایین فرم استفاده کنید."
        : "خطا در احراز هویت: نام کاربری یا رمز عبور وارد شده نادرست است. لطفاً نام کاربری و رمز عبور را بررسی کرده و مجدداً از دکمه «تست اتصال» استفاده نمایید.";
    }

    // Connection refused
    if (
      err.includes("connection refused") ||
      err.includes("refused") ||
      err.includes("could not connect to server") ||
      err.includes("10061") ||
      err.includes("errno 111") ||
      err.includes("errno 61") ||
      err.includes("actively refused")
    ) {
      return context === "discovery"
        ? "ارتباط با سرور برقرار نشد: اتصال از سوی سرور مقصد رد شد (Connection Refused). لطفاً بررسی کنید سرویس دیتابیس در پورت مشخص‌شده فعال باشد یا از «تست اتصال» استفاده کنید."
        : "ارتباط با سرور برقرار نشد: اتصال از سوی سرور مقصد رد شد (Connection Refused). لطفاً بررسی کنید سرویس دیتابیس روی هاست و پورت تعیین‌شده در حال اجرا باشد یا از «تست اتصال» استفاده نمایید.";
    }

    // Host unreachable / DNS / Network
    if (
      err.includes("getaddrinfo failed") ||
      err.includes("name or service not known") ||
      err.includes("no route to host") ||
      err.includes("network is unreachable") ||
      err.includes("network unreachable") ||
      err.includes("host unreachable") ||
      err.includes("network error")
    ) {
      return context === "discovery"
        ? "آدرس سرور (هاست) یافت نشد یا شبکه در دسترس نیست. لطفاً آدرس سرور و اتصال شبکه را بررسی نموده یا از دکمه «تست اتصال» استفاده کنید."
        : "آدرس سرور (هاست) یافت نشد یا ارتباط شبکه برقرار نیست. لطفاً آدرس هاست و اتصال شبکه را بررسی کرده و مجدداً «تست اتصال» را امتحان کنید.";
    }

    // Unsupported engine
    if (
      err.includes("not yet supported") ||
      err.includes("unsupported") ||
      err.includes("not supported")
    ) {
      return "شناسایی خودکار پایگاه‌داده برای این موتور در حال حاضر پشتیبانی نمی‌شود. لطفاً نام دیتابیس را به صورت دستی وارد کرده و از «تست اتصال» استفاده نمایید.";
    }

    // Database not found
    if (err.includes("does not exist") || err.includes("database not found")) {
      return "پایگاه‌داده مشخص‌شده بر روی این سرور یافت نشد. لطفاً نام دیتابیس را بررسی کرده و از دکمه «تست اتصال» در پایین فرم استفاده کنید.";
    }

    // If already in Persian and has no English error prefixes
    if (
      /[\u0600-\u06FF]/.test(error) &&
      !error.includes("Connection failed") &&
      !error.includes("Database discovery failed")
    ) {
      return error;
    }

    return context === "discovery"
      ? "خطا در شناسایی پایگاه‌های داده: امکان اتصال به سرور وجود ندارد. لطفاً آدرس سرور، پورت و مشخصات کاربری را بررسی کرده یا از دکمه «تست اتصال» در پایین فرم استفاده نمایید."
      : "خطا در برقراری اتصال به پایگاه‌داده. لطفاً آدرس سرور، پورت و مشخصات کاربری را بررسی کرده و مجدداً از دکمه «تست اتصال» استفاده کنید.";
  }

  translateTestMessage(
    msg: string | null | undefined,
    success: boolean,
  ): string {
    if (success) {
      return "اتصال به پایگاه‌داده با موفقیت برقرار شد.";
    }
    return this.translateErrorMessage(msg, "test");
  }

  toPersianNumber(num: number): string {
    const farsiDigits = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];
    return num.toString().replace(/\d/g, (d) => farsiDigits[parseInt(d, 10)]);
  }

  canDiscoverDatabases(): boolean {
    const val = this.form.getRawValue();
    const hasHost = Boolean(val.host && val.host.trim().length > 0);
    const validPort =
      typeof val.port === "number" && val.port > 0 && val.port <= 65535;
    const hasUsername = Boolean(val.username && val.username.trim().length > 0);
    const hasPassword = Boolean(
      this.connectionToEdit() ||
      (val.password && val.password.trim().length > 0),
    );
    return hasHost && validPort && hasUsername && hasPassword;
  }

  discoverDatabases(): void {
    if (this.isDiscoveringDatabases()) return;
    const val = this.form.getRawValue();
    if (!val.host || !val.port || !val.username) return;

    let customParams: Record<string, unknown> = {};
    if (val.custom_params_json?.trim()) {
      try {
        const parsed = JSON.parse(val.custom_params_json.trim());
        if (
          typeof parsed === "object" &&
          parsed !== null &&
          !Array.isArray(parsed)
        ) {
          customParams = parsed;
        }
      } catch {}
    }

    this.isDiscoveringDatabases.set(true);
    this.discoveryStatus.set("loading");
    this.discoveryMessage.set(null);

    this.connectionService
      .discoverDatabases({
        engine_type: val.engine_type,
        host: val.host,
        port: val.port,
        credentials: {
          username: val.username,
          password: val.password || undefined,
        },
        custom_params: customParams,
      })
      .subscribe({
        next: (res) => {
          this.isDiscoveringDatabases.set(false);
          if (res.success && res.databases) {
            this.discoveredDatabases.set(res.databases);
            this.discoveryStatus.set("success");
            const countStr = this.toPersianNumber(res.databases.length);
            this.discoveryMessage.set(`✓ ${countStr} پایگاه‌داده شناسایی شد`);
            if (res.databases.length > 0) {
              this.isDropdownOpen.set(true);
              if (!this.form.controls.database_name.value) {
                this.form.controls.database_name.setValue(res.databases[0]);
              }
            }
          } else {
            this.discoveryStatus.set("error");
            this.discoveryMessage.set(
              this.translateErrorMessage(res.message, "discovery"),
            );
          }
        },
        error: (err) => {
          this.isDiscoveringDatabases.set(false);
          this.discoveryStatus.set("error");
          const rawMsg =
            err.error?.detail || err.error?.message || err.message || "";
          this.discoveryMessage.set(
            this.translateErrorMessage(rawMsg, "discovery"),
          );
        },
      });
  }

  selectDiscoveredDatabase(db: string): void {
    this.form.controls.database_name.setValue(db);
    this.form.controls.database_name.markAsDirty();
    this.isDropdownOpen.set(false);
  }

  onComboboxFocus(): void {
    if (this.discoveredDatabases().length > 0) {
      this.isDropdownOpen.set(true);
    }
  }

  toggleDropdown(): void {
    if (this.discoveredDatabases().length > 0) {
      this.isDropdownOpen.update((v) => !v);
    }
  }

  @HostListener("document:click", ["$event"])
  onDocumentClick(event: MouseEvent): void {
    const target = event.target as HTMLElement;
    if (!target.closest(".combobox-container")) {
      this.isDropdownOpen.set(false);
    }
  }

  canTestConnection(): boolean {
    const val = this.form.getRawValue();
    const hasCreds = Boolean(
      val.username && (val.password || this.connectionToEdit()),
    );
    return Boolean(val.host && val.port && val.database_name && hasCreds);
  }

  testUnsavedConnection(): void {
    if (this.isTestingConnection()) return;
    const val = this.form.getRawValue();
    if (!val.host || !val.port || !val.database_name || !val.username) {
      this.form.controls.database_name.markAsTouched();
      return;
    }

    let customParams: Record<string, unknown> = {};
    if (val.custom_params_json?.trim()) {
      try {
        const parsed = JSON.parse(val.custom_params_json.trim());
        if (
          typeof parsed === "object" &&
          parsed !== null &&
          !Array.isArray(parsed)
        ) {
          customParams = parsed;
        }
      } catch {}
    }

    this.isTestingConnection.set(true);
    this.testResult.set(null);

    this.connectionService
      .testUnsavedConnection({
        engine_type: val.engine_type,
        host: val.host,
        port: val.port,
        database_name: val.database_name,
        credentials: {
          username: val.username,
          password: val.password || undefined,
        },
        custom_params: customParams,
      })
      .subscribe({
        next: (res) => {
          this.isTestingConnection.set(false);
          const translatedMsg = this.translateTestMessage(
            res.message,
            res.success,
          );
          this.testResult.set({
            ...res,
            message: translatedMsg,
          });
        },
        error: (err) => {
          this.isTestingConnection.set(false);
          const rawMsg =
            err.error?.detail || err.error?.message || err.message || "";
          const msg = this.translateErrorMessage(rawMsg, "test");
          this.testResult.set({
            success: false,
            latency_ms: 0,
            dialect: val.engine_type,
            message: msg,
            tested_at: new Date().toISOString(),
          });
        },
      });
  }

  onSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      if (this.form.controls.custom_params_json.invalid) {
        this.isCustomParamsOpen.set(true);
      }
      return;
    }

    const val = this.form.getRawValue();
    let customParams: Record<string, unknown> = {};
    const rawCustom = val.custom_params_json?.trim();
    if (rawCustom) {
      try {
        const parsed = JSON.parse(rawCustom);
        if (
          typeof parsed === "object" &&
          parsed !== null &&
          !Array.isArray(parsed)
        ) {
          customParams = parsed;
        } else {
          this.form.controls.custom_params_json.setErrors({
            invalidJson: "ورودی باید یک شیء معتبر JSON باشد",
          });
          this.isCustomParamsOpen.set(true);
          return;
        }
      } catch {
        this.form.controls.custom_params_json.setErrors({
          invalidJson: "خطا در ساختار JSON: فرمت وارد شده نامعتبر است",
        });
        this.isCustomParamsOpen.set(true);
        return;
      }
    }

    const creds: Record<string, unknown> = {
      username: val.username,
    };
    if (val.password && val.password.trim()) {
      creds["password"] = val.password;
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
      custom_params: customParams,
      cron_expression: val.cron_expression?.trim() || undefined,
    };

    this.save.emit(request);
  }
}

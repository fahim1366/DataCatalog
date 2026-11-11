import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { AuditService } from './services/audit.service';
import { AuditLogFilterParams, AuditLogItem } from './models/audit.models';

@Component({
  selector: 'app-audit-log-viewer',
  standalone: true,
  imports: [CommonModule, FormsModule, ScrollingModule],
  templateUrl: './audit-log-viewer.component.html',
  styleUrls: ['./audit-log-viewer.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AuditLogViewerComponent implements OnInit {
  private readonly auditService = inject(AuditService);

  /**
   * Optional custom items input. Defaults to loadedItems() fetched via auditService.
   */
  readonly itemsInput = input<AuditLogItem[] | undefined>(undefined, { alias: 'items' });

  /**
   * Optional initial filter inputs when embedded in parent detail views.
   */
  readonly entityTypeInput = input<string | undefined>(undefined, { alias: 'entityType' });
  readonly entityIdInput = input<string | undefined>(undefined, { alias: 'entityId' });

  // Core data signals
  readonly loadedItems = signal<AuditLogItem[]>([]);
  readonly items = computed<AuditLogItem[]>(() => this.itemsInput() ?? this.loadedItems());
  readonly isLoading = signal<boolean>(false);
  readonly isLoadingMore = signal<boolean>(false);
  readonly hasMore = signal<boolean>(false);
  readonly nextCursor = signal<string | null>(null);
  readonly errorMessage = signal<string | null>(null);

  // Filter signals
  readonly filterEntityType = signal<string>('');
  readonly filterEntityId = signal<string>('');
  readonly filterActorId = signal<string>('');
  readonly filterAction = signal<string>('');
  readonly filterStartTime = signal<string>('');
  readonly filterEndTime = signal<string>('');

  // Inspection modal / drawer state
  readonly selectedItem = signal<AuditLogItem | null>(null);

  /**
   * Prettified JSON string representation of the inspected record's diff payload.
   */
  readonly formattedDiffJson = computed<string>(() => {
    const item = this.selectedItem();
    if (!item || !item.diff_jsonb) return '{}';
    return JSON.stringify(item.diff_jsonb, null, 2);
  });

  ngOnInit(): void {
    if (this.entityTypeInput()) {
      this.filterEntityType.set(this.entityTypeInput()!);
    }
    if (this.entityIdInput()) {
      this.filterEntityId.set(this.entityIdInput()!);
    }

    if (!this.itemsInput()) {
      this.loadAuditLogs();
    }
  }

  /**
   * Loads initial page of audit logs applying currently configured filters.
   */
  loadAuditLogs(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);
    this.nextCursor.set(null);

    const filters: AuditLogFilterParams = this.buildFilterParams();

    this.auditService.getAuditLogs(filters).subscribe({
      next: (response) => {
        this.loadedItems.set(response.items);
        this.hasMore.set(response.page_info.has_next_page);
        this.nextCursor.set(response.page_info.next_cursor);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Failed to query audit logs';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  /**
   * Loads next keyset page when virtual scroll nears bottom.
   */
  loadNextPage(): void {
    if (!this.hasMore() || this.isLoading() || this.isLoadingMore() || !this.nextCursor()) {
      return;
    }

    this.isLoadingMore.set(true);
    const filters: AuditLogFilterParams = {
      ...this.buildFilterParams(),
      cursor: this.nextCursor()!,
    };

    this.auditService.getAuditLogs(filters).subscribe({
      next: (response) => {
        this.loadedItems.update((current) => [...current, ...response.items]);
        this.hasMore.set(response.page_info.has_next_page);
        this.nextCursor.set(response.page_info.next_cursor);
        this.isLoadingMore.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Failed to load additional audit logs';
        this.errorMessage.set(msg);
        this.isLoadingMore.set(false);
      },
    });
  }

  /**
   * Virtual scroll event listener for infinite keyset paging.
   */
  onScroll(scrolledIndex: number): void {
    const total = this.items().length;
    // Trigger when user is within 15 rows of the end
    if (total > 0 && scrolledIndex + 15 >= total) {
      this.loadNextPage();
    }
  }

  /**
   * Applies active filter values and reloads from first page.
   */
  applyFilters(): void {
    this.loadAuditLogs();
  }

  /**
   * Resets all filter values to empty defaults and reloads.
   */
  resetFilters(): void {
    this.filterEntityType.set('');
    this.filterEntityId.set('');
    this.filterActorId.set('');
    this.filterAction.set('');
    this.filterStartTime.set('');
    this.filterEndTime.set('');
    this.loadAuditLogs();
  }

  /**
   * Opens JSON diff inspection modal/drawer for the designated audit record.
   */
  inspectPayload(item: AuditLogItem): void {
    this.selectedItem.set(item);
  }

  /**
   * Closes the active inspection modal/drawer.
   */
  closeDetail(): void {
    this.selectedItem.set(null);
  }

  /**
   * Deterministic trackBy identity for CDK virtual scroll.
   */
  trackById(index: number, item: AuditLogItem): string {
    return item.id;
  }

  /**
   * Helper building filter parameters from reactive signals.
   */
  private buildFilterParams(): AuditLogFilterParams {
    const params: AuditLogFilterParams = {};
    if (this.filterEntityType()) params.entity_type = this.filterEntityType();
    if (this.filterEntityId()) params.entity_id = this.filterEntityId();
    if (this.filterActorId()) params.actor_id = this.filterActorId();
    if (this.filterAction()) params.action = this.filterAction();
    if (this.filterStartTime()) params.start_time = this.filterStartTime();
    if (this.filterEndTime()) params.end_time = this.filterEndTime();
    return params;
  }

  getActionBadgeClass(action: string): string {
    if (action.includes('CREATE') || action.includes('ADD')) {
      return 'badge-action-create';
    }
    if (action.includes('DELETE') || action.includes('REVOKE') || action.includes('DROP')) {
      return 'badge-action-delete';
    }
    if (action.includes('UPDATE') || action.includes('EDIT') || action.includes('MODIFY')) {
      return 'badge-action-update';
    }
    return 'badge-action-default';
  }

  getEntityTypeBadgeClass(entityType: string): string {
    switch (entityType.toUpperCase()) {
      case 'DATABASE':
        return 'badge-entity-database';
      case 'SCHEMA':
        return 'badge-entity-schema';
      case 'TABLE':
        return 'badge-entity-table';
      case 'COLUMN':
        return 'badge-entity-column';
      default:
        return 'badge-entity-default';
    }
  }
}


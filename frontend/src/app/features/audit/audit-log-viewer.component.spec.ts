import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { CdkVirtualScrollViewport, ScrollingModule } from '@angular/cdk/scrolling';
import { of, throwError } from 'rxjs';
import { AuditLogViewerComponent } from './audit-log-viewer.component';
import { AuditService } from './services/audit.service';
import { AuditLogItem, AuditLogResponse } from './models/audit.models';

function generateMockAuditLogs(count: number): AuditLogItem[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `00000000-0000-0000-0000-${i.toString().padStart(12, '0')}`,
    actor_id: `11111111-1111-1111-1111-${(i % 10).toString().padStart(12, '0')}`,
    actor_username: `auditor_${i % 5}@example.com`,
    ip_address: `192.168.1.${(i % 250) + 1}`,
    user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    action: i % 3 === 0 ? 'UPDATE_DESCRIPTION' : i % 3 === 1 ? 'CREATE_CONNECTION' : 'REVOKE_PERMISSION',
    entity_type: i % 2 === 0 ? 'TABLE' : 'DATABASE',
    entity_id: `22222222-2222-2222-2222-${i.toString().padStart(12, '0')}`,
    diff_jsonb: {
      field_modified: 'description',
      old_value: `Legacy description #${i}`,
      new_value: `Updated enterprise compliance description #${i}`,
      metadata: { severity: 'MEDIUM', audit_code: `AUD-${i}` },
    },
    created_at: new Date(Date.UTC(2026, 10, 1 + (i % 28), 12, 0, 0)).toISOString(),
  }));
}

describe('AuditLogViewerComponent (T039: CDK Virtual Scroll Audit Viewer & JSON Diff Inspection)', () => {
  let fixture: ComponentFixture<AuditLogViewerComponent>;
  let component: AuditLogViewerComponent;
  let auditService: AuditService;

  const mockResponse: AuditLogResponse = {
    items: generateMockAuditLogs(20),
    page_info: {
      has_next_page: true,
      next_cursor: 'cursor-token-page-2',
      limit: 20,
    },
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AuditLogViewerComponent, ScrollingModule],
      providers: [
        AuditService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    auditService = TestBed.inject(AuditService);
    vi.spyOn(auditService, 'getAuditLogs').mockReturnValue(of(mockResponse));

    fixture = TestBed.createComponent(AuditLogViewerComponent);
    component = fixture.componentInstance;
  });

  it('should create the AuditLogViewerComponent instance', () => {
    expect(component).toBeTruthy();
  });

  it('should load initial audit logs on init and display them in virtual scroll viewport', async () => {
    fixture.detectChanges();
    await fixture.whenStable();

    expect(auditService.getAuditLogs).toHaveBeenCalledTimes(1);
    expect(component.items().length).toBe(20);

    const viewportDebug = fixture.debugElement.query(By.directive(CdkVirtualScrollViewport));
    expect(viewportDebug).toBeTruthy();
  });

  it('should efficiently render 1,000 audit records in CDK virtual scroll viewport without DOM bloat', async () => {
    const mock1000 = generateMockAuditLogs(1000);
    vi.spyOn(auditService, 'getAuditLogs').mockReturnValue(
      of({
        items: mock1000,
        page_info: { has_next_page: false, next_cursor: null, limit: 1000 },
      })
    );

    fixture.detectChanges();
    await fixture.whenStable();

    const viewportDebug = fixture.debugElement.query(By.directive(CdkVirtualScrollViewport));
    expect(viewportDebug).toBeTruthy();
    const viewport = viewportDebug.componentInstance as CdkVirtualScrollViewport;

    Object.defineProperty(viewport.elementRef.nativeElement, 'clientHeight', {
      value: 520,
      configurable: true,
    });
    viewport.checkViewportSize();

    await Promise.resolve();
    fixture.detectChanges();
    await fixture.whenStable();

    // Verify viewport itemSize attribute
    const itemSizeAttr = viewportDebug.attributes['itemsize'] || viewportDebug.attributes['itemSize'];
    expect(itemSizeAttr).toBe('52');

    // Virtual scroll assertion: with 1,000 records, only bounded DOM rows should exist
    const renderedRows = fixture.debugElement.queryAll(By.css('.audit-row'));
    expect(renderedRows.length).toBeGreaterThan(0);
    expect(renderedRows.length).toBeLessThan(100);
    expect(renderedRows.length).not.toBe(1000);

    // Verify rendered audit item contents
    const firstRowText = renderedRows[0].nativeElement.textContent;
    expect(firstRowText).toContain('auditor_0@example.com');
    expect(firstRowText).toContain('UPDATE_DESCRIPTION');
  });

  it('should track audit items deterministically by unique id via trackById()', () => {
    const item = generateMockAuditLogs(1)[0];
    const trackResult = component.trackById(0, item);
    expect(trackResult).toBe(item.id);
  });

  it('should dispatch filters and query audit service with updated filter criteria', async () => {
    fixture.detectChanges();
    await fixture.whenStable();

    const getAuditLogsSpy = vi.spyOn(auditService, 'getAuditLogs').mockReturnValue(
      of({
        items: generateMockAuditLogs(5),
        page_info: { has_next_page: false, next_cursor: null, limit: 50 },
      })
    );

    // Update filter signals
    component.filterEntityType.set('TABLE');
    component.filterAction.set('UPDATE_DESCRIPTION');
    component.filterActorId.set('11111111-1111-1111-1111-000000000001');
    component.filterEntityId.set('22222222-2222-2222-2222-000000000001');

    component.applyFilters();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(getAuditLogsSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        entity_type: 'TABLE',
        action: 'UPDATE_DESCRIPTION',
        actor_id: '11111111-1111-1111-1111-000000000001',
        entity_id: '22222222-2222-2222-2222-000000000001',
      })
    );
  });

  it('should reset filters and reload audit records with default parameters', async () => {
    fixture.detectChanges();
    await fixture.whenStable();

    component.filterEntityType.set('DATABASE');
    component.filterAction.set('REVOKE_PERMISSION');

    const getAuditLogsSpy = vi.spyOn(auditService, 'getAuditLogs').mockReturnValue(
      of(mockResponse)
    );

    component.resetFilters();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.filterEntityType()).toBe('');
    expect(component.filterAction()).toBe('');
    expect(getAuditLogsSpy).toHaveBeenCalled();
  });

  it('should trigger infinite scroll loadNextPage() when scroll position nears bottom', async () => {
    fixture.detectChanges();
    await fixture.whenStable();

    const nextPageLogs = generateMockAuditLogs(10);
    const getAuditLogsSpy = vi.spyOn(auditService, 'getAuditLogs').mockReturnValue(
      of({
        items: nextPageLogs,
        page_info: { has_next_page: false, next_cursor: null, limit: 20 },
      })
    );

    // Initial items: 20 items loaded, next_cursor: cursor-token-page-2, hasMore: true
    expect(component.hasMore()).toBe(true);

    // User scrolls near the bottom of 20 items (e.g. index 16)
    component.onScroll(16);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(getAuditLogsSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        cursor: 'cursor-token-page-2',
      })
    );
    // 20 initial + 10 next = 30 total
    expect(component.items().length).toBe(30);
    expect(component.hasMore()).toBe(false);
  });

  it('should open inspection drawer/modal displaying formatted JSON diff payload and audit metadata', async () => {
    fixture.detectChanges();
    await fixture.whenStable();

    const testItem = component.items()[0];
    expect(component.selectedItem()).toBeNull();
    expect(fixture.debugElement.query(By.css('.audit-drawer'))).toBeNull();

    // Trigger inspection of first item
    component.inspectPayload(testItem);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.selectedItem()).toEqual(testItem);

    // Verify modal/drawer is visible
    const drawer = fixture.debugElement.query(By.css('.audit-drawer'));
    expect(drawer).toBeTruthy();

    // Verify JSON diff content
    const diffContainer = fixture.debugElement.query(By.css('.diff-payload-content'));
    expect(diffContainer).toBeTruthy();
    expect(diffContainer.nativeElement.textContent).toContain('Updated enterprise compliance description #0');
    expect(diffContainer.nativeElement.textContent).toContain('Legacy description #0');

    // Verify metadata displayed in drawer
    const drawerText = drawer.nativeElement.textContent;
    expect(drawerText).toContain(testItem.actor_username);
    expect(drawerText).toContain(testItem.ip_address!);
    expect(drawerText).toContain(testItem.action);
  });

  it('should close inspection drawer/modal when close button or backdrop is clicked', async () => {
    fixture.detectChanges();
    await fixture.whenStable();

    component.inspectPayload(component.items()[0]);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.selectedItem()).not.toBeNull();

    // Click close button
    const closeBtn = fixture.debugElement.query(By.css('.drawer-close-btn'));
    expect(closeBtn).toBeTruthy();
    closeBtn.nativeElement.click();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.selectedItem()).toBeNull();
    expect(fixture.debugElement.query(By.css('.audit-drawer'))).toBeNull();
  });

  it('should display empty state placeholder when no audit records match', async () => {
    vi.spyOn(auditService, 'getAuditLogs').mockReturnValue(
      of({ items: [], page_info: { has_next_page: false, next_cursor: null, limit: 50 } })
    );

    fixture.detectChanges();
    await fixture.whenStable();

    const emptyState = fixture.debugElement.query(By.css('.empty-audit-state'));
    expect(emptyState).toBeTruthy();
    expect(emptyState.nativeElement.textContent).toContain('No audit records found');
  });

  it('should display error alert message when audit service call fails', async () => {
    vi.spyOn(auditService, 'getAuditLogs').mockReturnValue(
      throwError(() => new Error('Server connection error 500'))
    );

    component.loadAuditLogs();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.errorMessage()).toContain('Server connection error 500');
    const alertBox = fixture.debugElement.query(By.css('.audit-error-alert'));
    expect(alertBox).toBeTruthy();
    expect(alertBox.nativeElement.textContent).toContain('Server connection error 500');
  });
});


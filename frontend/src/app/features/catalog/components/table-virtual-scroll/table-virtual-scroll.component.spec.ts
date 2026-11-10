import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { CdkVirtualScrollViewport, ScrollingModule } from '@angular/cdk/scrolling';
import { TableVirtualScrollComponent } from './table-virtual-scroll.component';
import { CatalogStore } from '../../stores/catalog.store';
import { SearchHit } from '../../models/catalog.models';

function generateMockItems(count: number): SearchHit[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `00000000-0000-0000-0000-${i.toString().padStart(12, '0')}`,
    entity_type: 'TABLE',
    name: `enterprise_table_${i.toString().padStart(6, '0')}`,
    database_name: `finance_db_${i % 5}`,
    schema_name: `reporting_schema_${i % 3}`,
    table_name: null,
    effective_description: `Automated test catalog table description for entity #${i}`,
    rank: 1.0 - (i / count),
    classification_level: i % 2 === 0 ? 'CONFIDENTIAL' : 'RESTRICTED',
    status: i % 20 === 0 ? 'DELETED' : 'ACTIVE',
  }));
}

describe('TableVirtualScrollComponent (T017: CDK Virtual Scroll 1,000 Items & Infinite Scroll)', () => {
  let fixture: ComponentFixture<TableVirtualScrollComponent>;
  let component: TableVirtualScrollComponent;
  let store: CatalogStore;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TableVirtualScrollComponent, ScrollingModule],
      providers: [
        CatalogStore,
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TableVirtualScrollComponent);
    component = fixture.componentInstance;
    store = TestBed.inject(CatalogStore);
  });

  it('should create the TableVirtualScrollComponent instance', () => {
    expect(component).toBeTruthy();
  });

  it('should efficiently render 1,000 items in CDK virtual scroll viewport without DOM bloat', async () => {
    const mock1000 = generateMockItems(1000);
    store.setSearchHits(mock1000);
    fixture.detectChanges();
    await fixture.whenStable();

    const viewportDebug = fixture.debugElement.query(By.directive(CdkVirtualScrollViewport));
    expect(viewportDebug).toBeTruthy();
    const viewport = viewportDebug.componentInstance as CdkVirtualScrollViewport;

    Object.defineProperty(viewport.elementRef.nativeElement, 'clientHeight', {
      value: 480,
      configurable: true,
    });
    viewport.checkViewportSize();

    // Allow CDK microtasks to settle
    await Promise.resolve();
    fixture.detectChanges();
    await fixture.whenStable();

    // Verify viewport element exists with itemSize="48"
    expect(viewportDebug.attributes['itemsize'] || viewportDebug.attributes['itemSize']).toBe('48');

    // Crucial Virtual Scroll Assertion:
    // With 1,000 items in the store, CDK Virtual Scroll should only render a bounded
    // number of DOM rows (buffer + visible items, typically 10-30 rows), never all 1,000.
    const renderedRows = fixture.debugElement.queryAll(By.css('.table-row'));
    expect(renderedRows.length).toBeGreaterThan(0);
    expect(renderedRows.length).toBeLessThan(100);
    expect(renderedRows.length).not.toBe(1000);

    // Verify first rendered item content
    const firstRow = renderedRows[0].nativeElement as HTMLElement;
    expect(firstRow.textContent).toContain('enterprise_table_000000');
    expect(firstRow.textContent).toContain('finance_db_0');
    expect(firstRow.textContent).toContain('reporting_schema_0');
  });

  it('should track items deterministically by unique id via trackById()', () => {
    const mockItem = generateMockItems(1)[0];
    const trackResult = component.trackById(0, mockItem);
    expect(trackResult).toBe(mockItem.id);
  });

  it('should trigger infinite scroll loadNextPage() when scroll position nears bottom', () => {
    const mock100 = generateMockItems(100);
    store.setSearchHits(mock100);

    const loadNextPageSpy = vi.spyOn(store, 'loadNextPage').mockImplementation(async () => {});
    const nearBottomSpy = vi.spyOn(component.nearBottom, 'emit');

    // Simulate store reporting more pages available
    vi.spyOn(store, 'hasMore').mockReturnValue(true);
    vi.spyOn(store, 'isLoading').mockReturnValue(false);

    // Scroll event near bottom (index 90 out of 100)
    component.onScroll(90);

    expect(loadNextPageSpy).toHaveBeenCalledTimes(1);
    expect(nearBottomSpy).toHaveBeenCalledTimes(1);
  });

  it('should not trigger loadNextPage() if store is already loading or has no more pages', () => {
    const mock100 = generateMockItems(100);
    store.setSearchHits(mock100);

    const loadNextPageSpy = vi.spyOn(store, 'loadNextPage').mockImplementation(async () => {});

    // Case 1: hasMore is false
    vi.spyOn(store, 'hasMore').mockReturnValue(false);
    vi.spyOn(store, 'isLoading').mockReturnValue(false);
    component.onScroll(90);
    expect(loadNextPageSpy).not.toHaveBeenCalled();

    // Case 2: isLoading is true
    vi.spyOn(store, 'hasMore').mockReturnValue(true);
    vi.spyOn(store, 'isLoading').mockReturnValue(true);
    component.onScroll(90);
    expect(loadNextPageSpy).not.toHaveBeenCalled();
  });

  it('should display empty placeholder when searchHits is empty and not loading', () => {
    store.setSearchHits([]);
    fixture.detectChanges();

    const emptyMessage = fixture.debugElement.query(By.css('.empty-catalog-state'));
    expect(emptyMessage).toBeTruthy();
    expect(emptyMessage.nativeElement.textContent).toContain('No catalog items found');
  });
});

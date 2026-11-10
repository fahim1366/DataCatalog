import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { CatalogStore } from './catalog.store';
import { SearchHit, SearchResponse } from '../models/catalog.models';

describe('CatalogStore (T016: Signal Store for Metadata Discovery)', () => {
  let store: CatalogStore;
  let httpTesting: HttpTestingController;

  const mockHitsPage1: SearchHit[] = [
    {
      id: '11111111-0000-0000-0000-000000000001',
      entity_type: 'TABLE',
      name: 'customers',
      database_name: 'prod_crm',
      schema_name: 'public',
      effective_description: 'CRM Customers table',
      rank: 0.95,
      classification_level: 'CONFIDENTIAL',
      status: 'ACTIVE',
    },
    {
      id: '11111111-0000-0000-0000-000000000002',
      entity_type: 'TABLE',
      name: 'customer_orders',
      database_name: 'prod_crm',
      schema_name: 'public',
      effective_description: 'Customer order history',
      rank: 0.88,
      classification_level: 'INTERNAL',
      status: 'ACTIVE',
    },
  ];

  const mockHitsPage2: SearchHit[] = [
    {
      id: '11111111-0000-0000-0000-000000000003',
      entity_type: 'TABLE',
      name: 'customer_reviews',
      database_name: 'prod_crm',
      schema_name: 'public',
      effective_description: 'Customer feedback ratings',
      rank: 0.72,
      classification_level: 'PUBLIC',
      status: 'ACTIVE',
    },
  ];

  const responsePage1: SearchResponse = {
    items: mockHitsPage1,
    page_info: {
      has_next_page: true,
      next_cursor: 'cursor_page_1_end',
      limit: 50,
    },
  };

  const responsePage2: SearchResponse = {
    items: mockHitsPage2,
    page_info: {
      has_next_page: false,
      next_cursor: null,
      limit: 50,
    },
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        CatalogStore,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });

    store = TestBed.inject(CatalogStore);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTesting.verify();
  });

  it('should initialize with empty signals and inactive loading state', () => {
    expect(store.searchHits()).toEqual([]);
    expect(store.tables()).toEqual([]);
    expect(store.isLoading()).toBe(false);
    expect(store.hasMore()).toBe(false);
  });

  it('should dispatch search query, update searchHits signal, and compute hasMore correctly', async () => {
    const searchPromise = store.search('customer');
    expect(store.isLoading()).toBe(true);

    const req = httpTesting.expectOne('/api/v1/search?q=customer&limit=50&cursor=');
    expect(req.request.method).toBe('GET');
    req.flush(responsePage1);

    await searchPromise;

    expect(store.isLoading()).toBe(false);
    expect(store.searchHits()).toEqual(mockHitsPage1);
    expect(store.hasMore()).toBe(true);
  });

  it('should append subsequent keyset pages using next_cursor on loadNextPage()', async () => {
    // Initial page fetch
    const p1 = store.search('customer');
    const req1 = httpTesting.expectOne('/api/v1/search?q=customer&limit=50&cursor=');
    req1.flush(responsePage1);
    await p1;

    expect(store.searchHits().length).toBe(2);
    expect(store.hasMore()).toBe(true);

    // Fetch next page with cursor
    const p2 = store.loadNextPage();
    const req2 = httpTesting.expectOne('/api/v1/search?q=customer&limit=50&cursor=cursor_page_1_end');
    expect(req2.request.method).toBe('GET');
    req2.flush(responsePage2);
    await p2;

    expect(store.isLoading()).toBe(false);
    expect(store.searchHits().length).toBe(3);
    expect(store.searchHits()).toEqual([...mockHitsPage1, ...mockHitsPage2]);
    expect(store.hasMore()).toBe(false);
  });

  it('should reset searchHits and cursor when a new search query is issued with reset=true', async () => {
    // Initial query
    const p1 = store.search('customer');
    httpTesting.expectOne('/api/v1/search?q=customer&limit=50&cursor=').flush(responsePage1);
    await p1;
    expect(store.searchHits().length).toBe(2);

    // New query with reset
    const p2 = store.search('orders', true);
    expect(store.searchHits()).toEqual([]);

    const req = httpTesting.expectOne('/api/v1/search?q=orders&limit=50&cursor=');
    req.flush({
      items: [mockHitsPage2[0]],
      page_info: { has_next_page: false, next_cursor: null, limit: 50 },
    });
    await p2;

    expect(store.searchHits().length).toBe(1);
    expect(store.searchHits()[0].name).toBe('customer_reviews');
  });

  it('should update effective_description in searchHits reactively via updateDescription()', async () => {
    const p1 = store.search('customer');
    httpTesting.expectOne('/api/v1/search?q=customer&limit=50&cursor=').flush(responsePage1);
    await p1;

    const targetId = mockHitsPage1[0].id;
    const patchPromise = store.updateDescription(targetId, 'Updated Enterprise Customer Master');

    const req = httpTesting.expectOne(`/api/v1/tables/${targetId}/description`);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ approved_description: 'Updated Enterprise Customer Master' });

    req.flush({
      id: targetId,
      effective_description: 'Updated Enterprise Customer Master',
    });
    await patchPromise;

    const updatedItem = store.searchHits().find((item: SearchHit) => item.id === targetId);
    expect(updatedItem?.effective_description).toBe('Updated Enterprise Customer Master');
  });

  it('should reset isLoading flag to false when search encounters an HTTP error', async () => {
    const p = store.search('error_query');
    expect(store.isLoading()).toBe(true);

    const req = httpTesting.expectOne('/api/v1/search?q=error_query&limit=50&cursor=');
    req.error(new ProgressEvent('Network error'), { status: 500, statusText: 'Internal Server Error' });

    await expect(p).rejects.toBeTruthy();
    expect(store.isLoading()).toBe(false);
  });
});

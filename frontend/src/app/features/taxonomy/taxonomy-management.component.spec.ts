import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { of, throwError } from 'rxjs';
import { describe, it, expect, beforeEach, vi } from 'vitest';

import {
  TaxonomyManagementComponent,
  CategoryRowData,
} from './taxonomy-management.component';
import {
  CategoryService,
  CategoryResponse,
  AvailableTableItem,
  CategoryAssignmentItem,
} from './services/category.service';
import { CategoryTreeNode } from '../catalog/models/catalog.models';

const mockCategoryTree: CategoryTreeNode[] = [
  {
    id: 'cat-root-1',
    name: 'حوزه مالی و اقتصادی',
    description: 'امور مالی، حسابداری و بودجه سازمان',
    parent_id: null,
    status: 'ACTIVE',
    assigned_tables_count: 1,
    tables: [
      {
        id: 'tbl-1',
        name: 'gl_general_ledger',
        schema_name: 'finance',
        database_name: 'CoreDB',
        row_count_estimate: 250000,
        column_count: 18,
      },
    ],
    children: [
      {
        id: 'cat-child-1',
        name: 'حسابداری کل',
        description: 'اسناد دفتر کل و تراز مالی',
        parent_id: 'cat-root-1',
        status: 'ACTIVE',
        assigned_tables_count: 0,
        tables: [],
        children: [
          {
            id: 'cat-subchild-1',
            name: 'تراکنش‌های ارزی',
            description: 'تسویه حساب ارزی',
            parent_id: 'cat-child-1',
            status: 'INACTIVE',
            assigned_tables_count: 0,
            tables: [],
            children: [],
          },
        ],
      },
    ],
  },
  {
    id: 'cat-root-2',
    name: 'منابع انسانی',
    description: 'پرسنل و احکام استخدامی',
    parent_id: null,
    status: 'ACTIVE',
    assigned_tables_count: 0,
    tables: [],
    children: [],
  },
];

const mockAvailableTables: AvailableTableItem[] = [
  {
    id: 'tbl-1',
    name: 'gl_general_ledger',
    schema_name: 'finance',
    database_name: 'CoreDB',
    row_count_estimate: 250000,
  },
  {
    id: 'tbl-2',
    name: 'payroll_records',
    schema_name: 'hr',
    database_name: 'CoreDB',
    row_count_estimate: 8000,
  },
];

const mockAssignments: CategoryAssignmentItem[] = [
  {
    id: 'assign-1',
    category_id: 'cat-root-1',
    category_name: 'حوزه مالی و اقتصادی',
    entity_type: 'TABLE',
    entity_id: 'tbl-1',
    table_name: 'gl_general_ledger',
    schema_name: 'finance',
    database_name: 'CoreDB',
  },
];

describe('TaxonomyManagementComponent', () => {
  let component: TaxonomyManagementComponent;
  let fixture: ComponentFixture<TaxonomyManagementComponent>;
  let categoryServiceMock: any;

  beforeEach(async () => {
    categoryServiceMock = {
      getCategoryTree: vi.fn().mockReturnValue(of(mockCategoryTree)),
      createCategory: vi.fn(),
      updateCategory: vi.fn(),
      deleteCategory: vi.fn(),
      getAssignments: vi.fn().mockReturnValue(of(mockAssignments)),
      assignTable: vi.fn().mockReturnValue(of({ success: true })),
      unassignTable: vi.fn().mockReturnValue(of(undefined)),
      getAvailableTables: vi.fn().mockReturnValue(of(mockAvailableTables)),
    };

    await TestBed.configureTestingModule({
      imports: [TaxonomyManagementComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideAnimationsAsync(),
        { provide: CategoryService, useValue: categoryServiceMock },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TaxonomyManagementComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the component instance and load initial category tree', () => {
    expect(component).toBeTruthy();
    expect(categoryServiceMock.getCategoryTree).toHaveBeenCalled();
    expect(component.rawCategoryTree().length).toBe(2);
    expect(component.totalCategoriesCount()).toBe(4);
    expect(component.totalAssignedTablesCount()).toBe(1);
  });

  it('should map category tree to PrimeNG TreeNodes with correct structure and data', () => {
    const nodes = component.treeNodes();
    expect(nodes.length).toBe(2);

    const root1 = nodes[0];
    expect(root1.key).toBe('cat-root-1');
    expect(root1.data?.name).toBe('حوزه مالی و اقتصادی');
    expect(root1.data?.assigned_tables_count).toBe(1);
    expect(root1.children?.length).toBe(1);

    const child1 = root1.children![0];
    expect(child1.key).toBe('cat-child-1');
    expect(child1.data?.parent_id).toBe('cat-root-1');
    expect(child1.children?.length).toBe(1);

    const subchild1 = child1.children![0];
    expect(subchild1.key).toBe('cat-subchild-1');
    expect(subchild1.data?.parent_id).toBe('cat-child-1');
    expect(subchild1.data?.status).toBe('INACTIVE');
  });

  it('should filter tree nodes by search term and status', () => {
    // Search by name
    component.searchTerm.set('تراکنش');
    const filteredBySearch = component.filteredTreeNodes();
    expect(filteredBySearch.length).toBe(1);
    expect(filteredBySearch[0].children?.[0]?.children?.[0]?.data?.name).toBe('تراکنش‌های ارزی');

    // Filter by status INACTIVE
    component.searchTerm.set('');
    component.statusFilter.set('INACTIVE');
    const filteredByStatus = component.filteredTreeNodes();
    expect(filteredByStatus.length).toBe(1);
    expect(filteredByStatus[0].key).toBe('cat-root-1');
  });

  it('should expand and collapse all tree nodes with toolbar actions', () => {
    component.expandAll();
    expect(component.expandedNodeKeys().size).toBe(4);
    expect(component.expandedNodeKeys().has('cat-root-1')).toBe(true);
    expect(component.expandedNodeKeys().has('cat-child-1')).toBe(true);

    component.collapseAll();
    expect(component.expandedNodeKeys().size).toBe(0);
  });

  it('should open root category creation modal with parent_id = null and submit to backend', () => {
    const newRoot: CategoryResponse = {
      id: 'cat-new-root',
      name: 'فناوری اطلاعات و زیرساخت',
      description: 'سامانه‌ها و سخت‌افزار',
      parent_id: null,
      status: 'ACTIVE',
      assigned_tables_count: 0,
    };
    categoryServiceMock.createCategory.mockReturnValue(of(newRoot));

    component.openAddRootModal();
    expect(component.isCategoryModalOpen()).toBe(true);
    expect(component.categoryModalMode()).toBe('create-root');
    expect(component.selectedParentCategory()).toBeNull();

    component.categoryForm.setValue({
      name: 'فناوری اطلاعات و زیرساخت',
      description: 'سامانه‌ها و سخت‌افزار',
      status: 'ACTIVE',
    });

    component.submitCategoryForm();

    expect(categoryServiceMock.createCategory).toHaveBeenCalledWith({
      name: 'فناوری اطلاعات و زیرساخت',
      description: 'سامانه‌ها و سخت‌افزار',
      parent_id: null,
      status: 'ACTIVE',
    });
    expect(component.isCategoryModalOpen()).toBe(false);
    expect(component.toastMessage()).toContain('با موفقیت ایجاد شد');
  });

  it('should open subcategory modal with parent_id populated and locked, and auto-expand parent upon creation', () => {
    const parent = component.treeNodes()[0].data!;
    const newChild: CategoryResponse = {
      id: 'cat-new-child',
      name: 'حسابداری صنعتی',
      description: 'بهای تمام شده کالا',
      parent_id: parent.id,
      status: 'ACTIVE',
      assigned_tables_count: 0,
    };
    categoryServiceMock.createCategory.mockReturnValue(of(newChild));

    component.openAddSubcategoryModal(parent);
    expect(component.isCategoryModalOpen()).toBe(true);
    expect(component.categoryModalMode()).toBe('create-child');
    expect(component.selectedParentCategory()?.id).toBe(parent.id);
    expect(component.selectedParentCategory()?.name).toBe('حوزه مالی و اقتصادی');

    component.categoryForm.setValue({
      name: 'حسابداری صنعتی',
      description: 'بهای تمام شده کالا',
      status: 'ACTIVE',
    });

    component.submitCategoryForm();

    expect(categoryServiceMock.createCategory).toHaveBeenCalledWith({
      name: 'حسابداری صنعتی',
      description: 'بهای تمام شده کالا',
      parent_id: parent.id,
      status: 'ACTIVE',
    });
    // Automatic expansion of parent node
    expect(component.expandedNodeKeys().has(parent.id)).toBe(true);
    expect(component.isCategoryModalOpen()).toBe(false);
    expect(component.toastMessage()).toContain('با موفقیت به');
  });

  it('should open edit category modal with existing data and save updates', () => {
    const catToEdit = component.treeNodes()[0].data!;
    const updatedCat: CategoryResponse = {
      id: catToEdit.id,
      name: 'امور مالی و اقتصادی کلان',
      description: 'شرح بروزرسانی شده',
      parent_id: null,
      status: 'ACTIVE',
      assigned_tables_count: 1,
    };
    categoryServiceMock.updateCategory.mockReturnValue(of(updatedCat));

    component.openEditCategoryModal(catToEdit);
    expect(component.isCategoryModalOpen()).toBe(true);
    expect(component.categoryModalMode()).toBe('edit');
    expect(component.editingCategory()?.id).toBe(catToEdit.id);
    expect(component.categoryForm.value.name).toBe('حوزه مالی و اقتصادی');

    component.categoryForm.setValue({
      name: 'امور مالی و اقتصادی کلان',
      description: 'شرح بروزرسانی شده',
      status: 'ACTIVE',
    });

    component.submitCategoryForm();

    expect(categoryServiceMock.updateCategory).toHaveBeenCalledWith(catToEdit.id, {
      name: 'امور مالی و اقتصادی کلان',
      description: 'شرح بروزرسانی شده',
      parent_id: null,
      status: 'ACTIVE',
    });
    expect(component.isCategoryModalOpen()).toBe(false);
    expect(component.toastMessage()).toContain('با موفقیت ذخیره شد');
  });

  it('should prevent deletion of categories containing subcategories with warning', () => {
    const parentWithChild = component.treeNodes()[0].data!;
    component.confirmDeleteCategory(parentWithChild);

    expect(component.isDeleteModalOpen()).toBe(true);
    expect(component.deleteSubcategoryCount()).toBe(1);

    // Attempt to execute delete should be blocked
    component.executeDeleteCategory();
    expect(categoryServiceMock.deleteCategory).not.toHaveBeenCalled();
  });

  it('should prevent deletion of categories containing assigned tables with warning', () => {
    const nodeWithTables = component.treeNodes()[0].data!;
    component.confirmDeleteCategory(nodeWithTables);

    expect(component.isDeleteModalOpen()).toBe(true);
    expect(component.deleteTableCount()).toBe(1);

    component.executeDeleteCategory();
    expect(categoryServiceMock.deleteCategory).not.toHaveBeenCalled();
  });

  it('should safely delete leaf category with 0 children and 0 tables', () => {
    const leafCategory = component.treeNodes()[1].data!; // HR (root-2 has 0 children and 0 tables)
    categoryServiceMock.deleteCategory.mockReturnValue(of(undefined));

    component.confirmDeleteCategory(leafCategory);
    expect(component.isDeleteModalOpen()).toBe(true);
    expect(component.deleteSubcategoryCount()).toBe(0);
    expect(component.deleteTableCount()).toBe(0);

    component.executeDeleteCategory();
    expect(categoryServiceMock.deleteCategory).toHaveBeenCalledWith(leafCategory.id);
    expect(component.isDeleteModalOpen()).toBe(false);
    expect(component.toastMessage()).toContain('با موفقیت حذف شد');
  });

  it('should open assign tables modal, load current assignments and available tables', () => {
    const category = component.treeNodes()[0].data!;
    component.openAssignTablesModal(category);

    expect(component.isAssignModalOpen()).toBe(true);
    expect(component.targetCategoryForAssign()?.id).toBe(category.id);
    expect(categoryServiceMock.getAssignments).toHaveBeenCalledWith(category.id, 'TABLE');
    expect(categoryServiceMock.getAvailableTables).toHaveBeenCalled();
    expect(component.currentAssignedTables().length).toBe(1);
    expect(component.availableCatalogTables().length).toBe(2);
  });

  it('should detect duplicate table assignment and prevent re-assignment', () => {
    const category = component.treeNodes()[0].data!;
    component.openAssignTablesModal(category);

    // tbl-1 is already in mockAssignments
    expect(component.isTableAssigned('tbl-1')).toBe(true);
    expect(component.isTableAssigned('tbl-2')).toBe(false);

    // Attempting to assign already assigned table shows toast and does not call API
    component.assignTableToCurrentCategory(mockAvailableTables[0]);
    expect(categoryServiceMock.assignTable).not.toHaveBeenCalled();
    expect(component.toastMessage()).toContain('پیش از این اختصاص یافته است');

    // Assigning unassigned table works
    component.assignTableToCurrentCategory(mockAvailableTables[1]);
    expect(categoryServiceMock.assignTable).toHaveBeenCalledWith(category.id, 'tbl-2');
  });

  it('should unassign table and refresh assignments', () => {
    const category = component.treeNodes()[0].data!;
    component.openAssignTablesModal(category);

    component.unassignTableFromCurrentCategory(mockAssignments[0]);
    expect(categoryServiceMock.unassignTable).toHaveBeenCalledWith(category.id, 'tbl-1');
  });
});

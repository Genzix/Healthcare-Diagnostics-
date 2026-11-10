from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.db.models import Q

from .models import Category, DiagnosticTest, HealthPackage
from .serializers import (
    CategorySerializer,
    DiagnosticTestSerializer,
    HealthPackageSerializer,
)


class CategoryListView(APIView):
    """
    API endpoint: Browse and create diagnostic test categories.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        categories = Category.objects.filter(is_active=True)
        serializer = CategorySerializer(categories, many=True)
        return Response(
            {
                "success": True,
                "count": categories.count(),
                "categories": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def post(self, request, *args, **kwargs):
        serializer = CategorySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors, "message": "Failed to create category."},
                status=status.HTTP_400_BAD_REQUEST
            )
        category = serializer.save()
        return Response(
            {
                "success": True,
                "message": f"Category '{category.name}' created successfully.",
                "category": CategorySerializer(category).data,
            },
            status=status.HTTP_201_CREATED
        )


class DiagnosticTestListView(APIView):
    """
    API endpoint: Browse, search, and create diagnostic tests.
    Query parameters for GET:
      - search: keyword search on name or code
      - category: filter by category ID or slug
      - sample_type: filter by sample type (BLOOD, URINE, etc.)
      - fasting: filter by fasting required (true/false)
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        queryset = DiagnosticTest.objects.filter(is_available=True).select_related('category')

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(code__icontains=search) |
                Q(description__icontains=search)
            )

        category = request.query_params.get('category')
        if category:
            if category.isdigit():
                queryset = queryset.filter(category_id=category)
            else:
                queryset = queryset.filter(category__slug=category)

        sample_type = request.query_params.get('sample_type')
        if sample_type:
            queryset = queryset.filter(sample_type__iexact=sample_type)

        fasting = request.query_params.get('fasting')
        if fasting is not None:
            queryset = queryset.filter(fasting_required=fasting.lower() in ['true', '1'])

        serializer = DiagnosticTestSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "count": queryset.count(),
                "tests": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def post(self, request, *args, **kwargs):
        serializer = DiagnosticTestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors, "message": "Failed to create diagnostic test."},
                status=status.HTTP_400_BAD_REQUEST
            )
        test = serializer.save()
        return Response(
            {
                "success": True,
                "message": f"Diagnostic test '{test.name}' created successfully.",
                "test": DiagnosticTestSerializer(test).data,
            },
            status=status.HTTP_201_CREATED
        )


class DiagnosticTestDetailView(APIView):
    """
    API endpoint: Retrieve, update, or remove individual diagnostic tests.
    """
    permission_classes = [AllowAny]

    def get(self, request, pk, *args, **kwargs):
        try:
            test = DiagnosticTest.objects.select_related('category').get(pk=pk)
        except DiagnosticTest.DoesNotExist:
            return Response(
                {"success": False, "message": "Diagnostic test not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = DiagnosticTestSerializer(test)
        return Response(
            {
                "success": True,
                "test": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def put(self, request, pk, *args, **kwargs):
        try:
            test = DiagnosticTest.objects.get(pk=pk)
        except DiagnosticTest.DoesNotExist:
            return Response(
                {"success": False, "message": "Diagnostic test not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = DiagnosticTestSerializer(test, data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors, "message": "Failed to update test."},
                status=status.HTTP_400_BAD_REQUEST
            )
        updated_test = serializer.save()
        return Response(
            {
                "success": True,
                "message": f"Diagnostic test '{updated_test.name}' updated successfully.",
                "test": DiagnosticTestSerializer(updated_test).data,
            },
            status=status.HTTP_200_OK
        )

    def patch(self, request, pk, *args, **kwargs):
        try:
            test = DiagnosticTest.objects.get(pk=pk)
        except DiagnosticTest.DoesNotExist:
            return Response(
                {"success": False, "message": "Diagnostic test not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = DiagnosticTestSerializer(test, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors, "message": "Failed to update test."},
                status=status.HTTP_400_BAD_REQUEST
            )
        updated_test = serializer.save()
        return Response(
            {
                "success": True,
                "message": f"Diagnostic test '{updated_test.name}' updated successfully.",
                "test": DiagnosticTestSerializer(updated_test).data,
            },
            status=status.HTTP_200_OK
        )

    def delete(self, request, pk, *args, **kwargs):
        try:
            test = DiagnosticTest.objects.get(pk=pk)
        except DiagnosticTest.DoesNotExist:
            return Response(
                {"success": False, "message": "Diagnostic test not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        test_name = test.name
        test.delete()
        return Response(
            {
                "success": True,
                "message": f"Diagnostic test '{test_name}' deleted successfully.",
            },
            status=status.HTTP_200_OK
        )


class HealthPackageListView(APIView):
    """
    API endpoint: Browse all health packages and create new packages.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        queryset = HealthPackage.objects.filter(is_available=True).prefetch_related('tests')

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(code__icontains=search) |
                Q(description__icontains=search)
            )

        serializer = HealthPackageSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "count": queryset.count(),
                "packages": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def post(self, request, *args, **kwargs):
        test_ids = request.data.get('test_ids', [])
        serializer = HealthPackageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors, "message": "Failed to create health package."},
                status=status.HTTP_400_BAD_REQUEST
            )
        package = serializer.save()
        if test_ids:
            package.tests.set(test_ids)

        return Response(
            {
                "success": True,
                "message": f"Health package '{package.name}' created successfully.",
                "package": HealthPackageSerializer(package).data,
            },
            status=status.HTTP_201_CREATED
        )


class HealthPackageDetailView(APIView):
    """
    API endpoint: Retrieve, update, or remove health packages.
    """
    permission_classes = [AllowAny]

    def get(self, request, pk, *args, **kwargs):
        try:
            package = HealthPackage.objects.prefetch_related('tests').get(pk=pk)
        except HealthPackage.DoesNotExist:
            return Response(
                {"success": False, "message": "Health package not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = HealthPackageSerializer(package)
        return Response(
            {
                "success": True,
                "package": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def put(self, request, pk, *args, **kwargs):
        try:
            package = HealthPackage.objects.get(pk=pk)
        except HealthPackage.DoesNotExist:
            return Response(
                {"success": False, "message": "Health package not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = HealthPackageSerializer(package, data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors, "message": "Failed to update package."},
                status=status.HTTP_400_BAD_REQUEST
            )
        updated_package = serializer.save()
        if 'test_ids' in request.data:
            updated_package.tests.set(request.data.get('test_ids', []))

        return Response(
            {
                "success": True,
                "message": f"Health package '{updated_package.name}' updated successfully.",
                "package": HealthPackageSerializer(updated_package).data,
            },
            status=status.HTTP_200_OK
        )

    def patch(self, request, pk, *args, **kwargs):
        try:
            package = HealthPackage.objects.get(pk=pk)
        except HealthPackage.DoesNotExist:
            return Response(
                {"success": False, "message": "Health package not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = HealthPackageSerializer(package, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors, "message": "Failed to update package."},
                status=status.HTTP_400_BAD_REQUEST
            )
        updated_package = serializer.save()
        if 'test_ids' in request.data:
            updated_package.tests.set(request.data.get('test_ids', []))

        return Response(
            {
                "success": True,
                "message": f"Health package '{updated_package.name}' updated successfully.",
                "package": HealthPackageSerializer(updated_package).data,
            },
            status=status.HTTP_200_OK
        )

    def delete(self, request, pk, *args, **kwargs):
        try:
            package = HealthPackage.objects.get(pk=pk)
        except HealthPackage.DoesNotExist:
            return Response(
                {"success": False, "message": "Health package not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        package_name = package.name
        package.delete()
        return Response(
            {
                "success": True,
                "message": f"Health package '{package_name}' deleted successfully.",
            },
            status=status.HTTP_200_OK
        )


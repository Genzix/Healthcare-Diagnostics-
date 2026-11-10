from django.core.management.base import BaseCommand
from diagnostics.models import Category, DiagnosticTest, HealthPackage
from decimal import Decimal


class Command(BaseCommand):
    help = "Seeds common diagnostic test categories, individual tests, and health packages."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding diagnostic categories and tests..."))

        # 1. Categories
        categories_data = [
            {"name": "Pathology / Blood", "slug": "pathology-blood", "description": "Routine and specialized blood tests", "icon": "blood-drop"},
            {"name": "Diabetes Care", "slug": "diabetes-care", "description": "Blood glucose, HbA1c, and insulin tests", "icon": "activity"},
            {"name": "Cardiac & Lipid Care", "slug": "cardiac-lipid", "description": "Heart health and lipid profile tests", "icon": "heart"},
            {"name": "Thyroid Care", "slug": "thyroid-care", "description": "Thyroid hormones and antibody panels", "icon": "shield"},
            {"name": "Kidney & Liver Care", "slug": "kidney-liver", "description": "Liver and renal function panels", "icon": "droplet"},
        ]

        cat_objs = {}
        for cdata in categories_data:
            cat, created = Category.objects.get_or_create(
                slug=cdata["slug"],
                defaults={
                    "name": cdata["name"],
                    "description": cdata["description"],
                    "icon": cdata["icon"],
                    "is_active": True,
                }
            )
            cat_objs[cdata["slug"]] = cat
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + Created Category: {cat.name}"))

        # 2. Diagnostic Tests
        tests_data = [
            {
                "name": "Complete Blood Count (CBC)",
                "code": "CBC-01",
                "category": cat_objs["pathology-blood"],
                "description": "Evaluates overall health and checks for infection, anemia, and immune disorders.",
                "sample_type": "BLOOD",
                "fasting_required": False,
                "fasting_duration_hours": 0,
                "turnaround_time_hours": 8,
                "preparation_instructions": "No special fasting required.",
                "base_price": Decimal("299.00"),
            },
            {
                "name": "Fasting Blood Glucose (FBG)",
                "code": "FBG-01",
                "category": cat_objs["diabetes-care"],
                "description": "Measures blood glucose levels after fasting to screen for prediabetes and diabetes.",
                "sample_type": "BLOOD",
                "fasting_required": true if False else True,
                "fasting_duration_hours": 8,
                "turnaround_time_hours": 6,
                "preparation_instructions": "Fast for 8 to 10 hours prior to sample collection. Water is allowed.",
                "base_price": Decimal("120.00"),
            },
            {
                "name": "HbA1c (Glycated Hemoglobin)",
                "code": "HBA1C-01",
                "category": cat_objs["diabetes-care"],
                "description": "Provides an average of your blood sugar levels over the past 2 to 3 months.",
                "sample_type": "BLOOD",
                "fasting_required": False,
                "fasting_duration_hours": 0,
                "turnaround_time_hours": 12,
                "preparation_instructions": "No fasting required. Can be done anytime of the day.",
                "base_price": Decimal("450.00"),
            },
            {
                "name": "Lipid Profile",
                "code": "LIPID-01",
                "category": cat_objs["cardiac-lipid"],
                "description": "Measures Total Cholesterol, HDL, LDL, VLDL, and Triglycerides to assess heart risk.",
                "sample_type": "BLOOD",
                "fasting_required": True,
                "fasting_duration_hours": 10,
                "turnaround_time_hours": 12,
                "preparation_instructions": "Fast for 10-12 hours prior to collection. Avoid high-fat dinner the night before.",
                "base_price": Decimal("550.00"),
            },
            {
                "name": "Thyroid Profile Total (T3, T4, TSH)",
                "code": "THY-01",
                "category": cat_objs["thyroid-care"],
                "description": "Evaluates thyroid gland function and diagnoses hypo/hyperthyroidism.",
                "sample_type": "BLOOD",
                "fasting_required": True,
                "fasting_duration_hours": 8,
                "turnaround_time_hours": 12,
                "preparation_instructions": "Morning sample preferred before taking thyroid medication.",
                "base_price": Decimal("400.00"),
            },
            {
                "name": "Liver Function Test (LFT)",
                "code": "LFT-01",
                "category": cat_objs["kidney-liver"],
                "description": "Measures bilirubin, SGOT, SGPT, alkaline phosphatase, and protein levels.",
                "sample_type": "BLOOD",
                "fasting_required": True,
                "fasting_duration_hours": 8,
                "turnaround_time_hours": 12,
                "preparation_instructions": "Fast for 8-10 hours prior to sample collection.",
                "base_price": Decimal("650.00"),
            },
            {
                "name": "Kidney / Renal Function Test (KFT)",
                "code": "KFT-01",
                "category": cat_objs["kidney-liver"],
                "description": "Measures urea, creatinine, uric acid, and electrolytes to assess kidney health.",
                "sample_type": "BLOOD",
                "fasting_required": False,
                "fasting_duration_hours": 0,
                "turnaround_time_hours": 12,
                "preparation_instructions": "Stay well-hydrated. No special fasting strictly required.",
                "base_price": Decimal("600.00"),
            },
            {
                "name": "Vitamin D (25-OH)",
                "code": "VITD-01",
                "category": cat_objs["pathology-blood"],
                "description": "Measures Vitamin D levels essential for bone health and immunity.",
                "sample_type": "BLOOD",
                "fasting_required": False,
                "fasting_duration_hours": 0,
                "turnaround_time_hours": 24,
                "preparation_instructions": "No special preparation needed.",
                "base_price": Decimal("850.00"),
            },
            {
                "name": "Vitamin B12",
                "code": "VITB12-01",
                "category": cat_objs["pathology-blood"],
                "description": "Checks Vitamin B12 levels vital for red blood cell formation and nerve health.",
                "sample_type": "BLOOD",
                "fasting_required": True,
                "fasting_duration_hours": 8,
                "turnaround_time_hours": 24,
                "preparation_instructions": "Fast for 8 hours prior to blood draw.",
                "base_price": Decimal("750.00"),
            },
        ]

        test_objs = {}
        for tdata in tests_data:
            test, created = DiagnosticTest.objects.get_or_create(
                code=tdata["code"],
                defaults={
                    "name": tdata["name"],
                    "category": tdata["category"],
                    "description": tdata["description"],
                    "sample_type": tdata["sample_type"],
                    "fasting_required": tdata["fasting_required"],
                    "fasting_duration_hours": tdata["fasting_duration_hours"],
                    "turnaround_time_hours": tdata["turnaround_time_hours"],
                    "preparation_instructions": tdata["preparation_instructions"],
                    "base_price": tdata["base_price"],
                    "is_available": True,
                }
            )
            test_objs[tdata["code"]] = test
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + Created Test: {test.name} (₹{test.base_price})"))

        # 3. Health Packages
        packages_data = [
            {
                "name": "Comprehensive Full Body Health Check",
                "code": "PKG-FULL-01",
                "description": "Complete checkup including CBC, Fasting Glucose, Lipid Profile, Liver Function, and Kidney Function.",
                "price": Decimal("1499.00"),
                "market_price": Decimal("2219.00"),
                "preparation_instructions": "Requires 10-12 hours overnight fasting. Drink water only.",
                "tests": ["CBC-01", "FBG-01", "LIPID-01", "LFT-01", "KFT-01"],
            },
            {
                "name": "Diabetes Screening & Care Package",
                "code": "PKG-DIAB-01",
                "description": "Essential bundle for diabetic patients: Fasting Glucose, HbA1c, and Lipid Profile.",
                "price": Decimal("899.00"),
                "market_price": Decimal("1120.00"),
                "preparation_instructions": "Requires 8-10 hours fasting for glucose and lipid tests.",
                "tests": ["FBG-01", "HBA1C-01", "LIPID-01"],
            },
            {
                "name": "Executive Wellness Master Package",
                "code": "PKG-EXEC-01",
                "description": "Executive panel including Full Body profile plus Vitamin D and Vitamin B12.",
                "price": Decimal("2499.00"),
                "market_price": Decimal("3819.00"),
                "preparation_instructions": "Fast for 10-12 hours prior to collection. Water is allowed.",
                "tests": ["CBC-01", "FBG-01", "LIPID-01", "LFT-01", "KFT-01", "THY-01", "VITD-01", "VITB12-01"],
            },
        ]

        for pdata in packages_data:
            pkg, created = HealthPackage.objects.get_or_create(
                code=pdata["code"],
                defaults={
                    "name": pdata["name"],
                    "description": pdata["description"],
                    "price": pdata["price"],
                    "market_price": pdata["market_price"],
                    "preparation_instructions": pdata["preparation_instructions"],
                    "is_available": True,
                }
            )
            if created:
                for code in pdata["tests"]:
                    if code in test_objs:
                        pkg.tests.add(test_objs[code])
                self.stdout.write(self.style.SUCCESS(f"  + Created Health Package: {pkg.name} (₹{pkg.price})"))

        self.stdout.write(self.style.SUCCESS("Diagnostic catalogue seeding completed successfully!"))


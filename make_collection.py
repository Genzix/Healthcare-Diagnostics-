import json
import uuid

def make_req(name, method, path, body=None, auth=False, query=None, test_script=None, example_response=None, status_code=200):
    url_parts = path.strip("/").split("/")
    headers = [{"key": "Content-Type", "value": "application/json"}]
    if auth:
        headers.append({"key": "Authorization", "value": "Bearer {{accessToken}}"})

    req_obj = {
        "method": method,
        "header": headers,
        "url": {
            "raw": "{{baseUrl}}/" + path.lstrip("/"),
            "host": ["{{baseUrl}}"],
            "path": url_parts
        }
    }
    if query:
        req_obj["url"]["query"] = [{"key": k, "value": v} for k, v in query.items()]
        query_str = "&".join(f"{k}={v}" for k, v in query.items())
        req_obj["url"]["raw"] += f"?{query_str}"

    if body is not None:
        req_obj["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {
                "raw": {
                    "language": "json"
                }
            }
        }

    item = {
        "name": name,
        "request": req_obj,
        "response": []
    }

    if test_script:
        item["event"] = [
            {
                "listen": "test",
                "script": {
                    "exec": test_script if isinstance(test_script, list) else [test_script],
                    "type": "text/javascript"
                }
            }
        ]

    if example_response:
        item["response"].append({
            "name": f"{status_code} " + ("OK" if status_code == 200 else "Created" if status_code == 201 else "Response"),
            "originalRequest": req_obj,
            "status": "OK" if status_code == 200 else "Created",
            "code": status_code,
            "_postman_previewlanguage": "json",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": json.dumps(example_response, indent=2)
        })

    return item

# Build Flow-Wise Structure
flows = [
    {
        "name": "Flow 1 - Patient Registration, Authentication & Profile",
        "description": "Step 1 to Step 3 of user journey: Phone OTP registration, login, JWT token management, profile, and family members.",
        "item": [
            make_req(
                name="1.1 Register - Send Phone OTP",
                method="POST",
                path="/api/register/send-otp/",
                body={
                    "phone_number": "+919876543210",
                    "first_name": "Rahul",
                    "last_name": "Sharma",
                    "email": "rahul.sharma@example.com",
                    "role": "PATIENT"
                },
                example_response={
                    "success": True,
                    "message": "OTP sent successfully to +919876543210.",
                    "phone_number": "+919876543210",
                    "otp_expiry_minutes": 5,
                    "debug_otp": "482910"
                },
                status_code=200
            ),
            make_req(
                name="1.2 Register - Verify OTP & Receive JWT Tokens",
                method="POST",
                path="/api/register/verify-otp/",
                body={
                    "phone_number": "+919876543210",
                    "otp": "482910",
                    "first_name": "Rahul",
                    "last_name": "Sharma",
                    "email": "rahul.sharma@example.com",
                    "password": "SecurePassword123!"
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.tokens && res.tokens.access) {",
                    "    pm.collectionVariables.set('accessToken', res.tokens.access);",
                    "    pm.collectionVariables.set('refreshToken', res.tokens.refresh);",
                    "    console.log('Saved JWT tokens to collection variables.');",
                    "}"
                ],
                example_response={
                    "success": True,
                    "message": "Account created successfully.",
                    "user": {
                        "id": 1,
                        "phone_number": "+919876543210",
                        "first_name": "Rahul",
                        "last_name": "Sharma",
                        "email": "rahul.sharma@example.com",
                        "date_of_birth": None,
                        "age": None,
                        "gender": None,
                        "city": "",
                        "address": "",
                        "pincode": "",
                        "role": "PATIENT",
                        "is_phone_verified": True
                    },
                    "tokens": {
                        "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                    }
                },
                status_code=201
            ),
            make_req(
                name="1.3 Register - Resend OTP (with Cooldown Guard)",
                method="POST",
                path="/api/register/resend-otp/",
                body={
                    "phone_number": "+919876543210"
                },
                example_response={
                    "success": True,
                    "message": "A new OTP has been sent to +919876543210.",
                    "phone_number": "+919876543210",
                    "otp_expiry_minutes": 5,
                    "debug_otp": "617283"
                },
                status_code=200
            ),
            make_req(
                name="1.4 Login - Send OTP",
                method="POST",
                path="/api/login/send-otp/",
                body={
                    "phone_number": "+919876543210"
                },
                example_response={
                    "success": True,
                    "message": "Login OTP sent successfully to +919876543210.",
                    "phone_number": "+919876543210",
                    "otp_expiry_minutes": 5,
                    "debug_otp": "839102"
                },
                status_code=200
            ),
            make_req(
                name="1.5 Login - Verify OTP (Issues JWT Tokens)",
                method="POST",
                path="/api/login/verify-otp/",
                body={
                    "phone_number": "+919876543210",
                    "otp": "839102"
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.tokens && res.tokens.access) {",
                    "    pm.collectionVariables.set('accessToken', res.tokens.access);",
                    "    pm.collectionVariables.set('refreshToken', res.tokens.refresh);",
                    "}"
                ],
                example_response={
                    "success": True,
                    "message": "Login successful.",
                    "user": {
                        "id": 1,
                        "phone_number": "+919876543210",
                        "first_name": "Rahul",
                        "last_name": "Sharma",
                        "email": "rahul.sharma@example.com",
                        "role": "PATIENT"
                    },
                    "tokens": {
                        "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                    }
                },
                status_code=200
            ),
            make_req(
                name="1.6 Login - Password Authentication (Alternative)",
                method="POST",
                path="/api/login/password/",
                body={
                    "phone_number": "+919876543210",
                    "password": "SecurePassword123!"
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.tokens && res.tokens.access) {",
                    "    pm.collectionVariables.set('accessToken', res.tokens.access);",
                    "}"
                ],
                example_response={
                    "success": True,
                    "message": "Login successful.",
                    "tokens": {
                        "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                    }
                },
                status_code=200
            ),
            make_req(
                name="1.7 JWT - Refresh Access Token",
                method="POST",
                path="/api/token/refresh/",
                body={
                    "refresh": "{{refreshToken}}"
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.access) {",
                    "    pm.collectionVariables.set('accessToken', res.access);",
                    "}"
                ],
                example_response={
                    "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                },
                status_code=200
            ),
            make_req(
                name="1.8 JWT - Verify Access Token",
                method="POST",
                path="/api/token/verify/",
                body={
                    "token": "{{accessToken}}"
                },
                example_response={},
                status_code=200
            ),
            make_req(
                name="1.9 Profile - Get Current User Profile",
                method="GET",
                path="/api/profile/",
                auth=True,
                example_response={
                    "success": True,
                    "user": {
                        "id": 1,
                        "phone_number": "+919876543210",
                        "first_name": "Rahul",
                        "last_name": "Sharma",
                        "email": "rahul.sharma@example.com",
                        "date_of_birth": "1992-05-14",
                        "age": 34,
                        "gender": "MALE",
                        "city": "Hyderabad",
                        "address": "Flat 302, Green Meadows, Madhapur",
                        "pincode": "500081",
                        "role": "PATIENT",
                        "is_phone_verified": True
                    }
                },
                status_code=200
            ),
            make_req(
                name="1.10 Profile - Update Basic Profile",
                method="PUT",
                path="/api/profile/",
                auth=True,
                body={
                    "first_name": "Rahul",
                    "last_name": "Sharma",
                    "date_of_birth": "1992-05-14",
                    "gender": "MALE",
                    "city": "Hyderabad",
                    "address": "Flat 302, Green Meadows, Madhapur",
                    "pincode": "500081"
                },
                example_response={
                    "success": True,
                    "message": "Profile updated successfully.",
                    "user": {
                        "id": 1,
                        "first_name": "Rahul",
                        "last_name": "Sharma",
                        "date_of_birth": "1992-05-14",
                        "age": 34,
                        "gender": "MALE",
                        "city": "Hyderabad",
                        "address": "Flat 302, Green Meadows, Madhapur",
                        "pincode": "500081"
                    }
                },
                status_code=200
            ),
            make_req(
                name="1.11 Family Profiles - List Family Members",
                method="GET",
                path="/api/family-members/",
                auth=True,
                example_response=[
                    {
                        "id": 1,
                        "name": "Sunita Sharma",
                        "relationship": "SPOUSE",
                        "gender": "FEMALE",
                        "date_of_birth": "1994-08-20",
                        "age": 32,
                        "phone_number": "+919876500000",
                        "email": "sunita@example.com"
                    }
                ],
                status_code=200
            ),
            make_req(
                name="1.12 Family Profiles - Add Family Member",
                method="POST",
                path="/api/family-members/",
                auth=True,
                body={
                    "name": "Sunita Sharma",
                    "relationship": "SPOUSE",
                    "gender": "FEMALE",
                    "date_of_birth": "1994-08-20",
                    "phone_number": "+919876500000",
                    "email": "sunita@example.com"
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.family_member && res.family_member.id) {",
                    "    pm.collectionVariables.set('familyMemberId', res.family_member.id);",
                    "}"
                ],
                example_response={
                    "success": True,
                    "message": "Family member added successfully.",
                    "family_member": {
                        "id": 1,
                        "name": "Sunita Sharma",
                        "relationship": "SPOUSE",
                        "gender": "FEMALE",
                        "date_of_birth": "1994-08-20",
                        "age": 32,
                        "phone_number": "+919876500000"
                    }
                },
                status_code=201
            ),
            make_req(
                name="1.13 Family Profiles - Update Family Member",
                method="PUT",
                path="/api/family-members/{{familyMemberId}}/",
                auth=True,
                body={
                    "name": "Sunita Sharma",
                    "relationship": "SPOUSE",
                    "gender": "FEMALE",
                    "date_of_birth": "1994-08-20",
                    "phone_number": "+919876500000"
                },
                example_response={
                    "success": True,
                    "message": "Family member updated successfully.",
                    "family_member": {
                        "id": 1,
                        "name": "Sunita Sharma",
                        "relationship": "SPOUSE"
                    }
                },
                status_code=200
            ),
            make_req(
                name="1.14 Logout - Invalidate Session",
                method="POST",
                path="/api/logout/",
                auth=True,
                body={},
                example_response={
                    "success": True,
                    "message": "Logged out successfully."
                },
                status_code=200
            )
        ]
    },
    {
        "name": "Flow 2 - Diagnostics Catalogue & Health Packages",
        "description": "Step 4 of user journey: Explore categories, browse tests, review preparation instructions, and health packages.",
        "item": [
            make_req(
                name="2.1 Categories - List All",
                method="GET",
                path="/api/categories/",
                example_response={
                    "success": True,
                    "count": 2,
                    "categories": [
                        {"id": 1, "name": "Pathology / Blood", "slug": "pathology-blood", "description": "Routine blood tests", "tests_count": 8},
                        {"id": 2, "name": "Diabetes Care", "slug": "diabetes-care", "description": "Blood glucose and HbA1c", "tests_count": 3}
                    ]
                },
                status_code=200
            ),
            make_req(
                name="2.2 Categories - Create New Category (Admin)",
                method="POST",
                path="/api/categories/",
                body={
                    "name": "Cardiac & Lipid Care",
                    "description": "Heart risk profile tests",
                    "icon": "heart"
                },
                example_response={
                    "success": True,
                    "message": "Category 'Cardiac & Lipid Care' created successfully.",
                    "category": {
                        "id": 3,
                        "name": "Cardiac & Lipid Care",
                        "slug": "cardiac-lipid-care",
                        "description": "Heart risk profile tests",
                        "icon": "heart"
                    }
                },
                status_code=201
            ),
            make_req(
                name="2.3 Tests - Browse & Search Tests",
                method="GET",
                path="/api/tests/",
                query={"search": "glucose", "fasting": "true"},
                example_response={
                    "success": True,
                    "count": 1,
                    "tests": [
                        {
                            "id": 1,
                            "name": "Fasting Blood Glucose",
                            "code": "FBG-01",
                            "category": 1,
                            "category_name": "Diabetes Care",
                            "sample_type": "BLOOD",
                            "fasting_required": True,
                            "fasting_duration_hours": 8,
                            "turnaround_time_hours": 6,
                            "preparation_instructions": "Fast for 8 to 10 hours prior to sample collection.",
                            "base_price": "120.00",
                            "is_available": True
                        }
                    ]
                },
                status_code=200
            ),
            make_req(
                name="2.4 Tests - Create Diagnostic Test (Admin)",
                method="POST",
                path="/api/tests/",
                body={
                    "name": "Complete Blood Count (CBC)",
                    "code": "CBC-01",
                    "category": 1,
                    "description": "Evaluates overall health and detects anemia, infection, and leukemia.",
                    "sample_type": "BLOOD",
                    "fasting_required": False,
                    "fasting_duration_hours": 0,
                    "turnaround_time_hours": 8,
                    "preparation_instructions": "No special fasting required.",
                    "base_price": "299.00",
                    "is_available": True
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.test && res.test.id) {",
                    "    pm.collectionVariables.set('testId', res.test.id);",
                    "}"
                ],
                example_response={
                    "success": True,
                    "message": "Diagnostic test 'Complete Blood Count (CBC)' created successfully.",
                    "test": {
                        "id": 2,
                        "name": "Complete Blood Count (CBC)",
                        "code": "CBC-01",
                        "base_price": "299.00"
                    }
                },
                status_code=201
            ),
            make_req(
                name="2.5 Tests - Get Test Details",
                method="GET",
                path="/api/tests/{{testId}}/",
                example_response={
                    "success": True,
                    "test": {
                        "id": 1,
                        "name": "Complete Blood Count (CBC)",
                        "code": "CBC-01",
                        "category_name": "Pathology / Blood",
                        "sample_type": "BLOOD",
                        "fasting_required": False,
                        "preparation_instructions": "No special preparation needed.",
                        "base_price": "299.00"
                    }
                },
                status_code=200
            ),
            make_req(
                name="2.6 Packages - List Health Packages",
                method="GET",
                path="/api/packages/",
                example_response={
                    "success": True,
                    "count": 1,
                    "packages": [
                        {
                            "id": 1,
                            "name": "Comprehensive Full Body Checkup",
                            "code": "PKG-FULL-01",
                            "price": "1499.00",
                            "market_price": "2200.00",
                            "discount_percentage": 32,
                            "tests_count": 5,
                            "tests": [
                                {"id": 1, "name": "Fasting Blood Glucose", "base_price": "120.00"},
                                {"id": 2, "name": "Complete Blood Count (CBC)", "base_price": "299.00"}
                            ]
                        }
                    ]
                },
                status_code=200
            ),
            make_req(
                name="2.7 Packages - Create Health Package (Admin)",
                method="POST",
                path="/api/packages/",
                body={
                    "name": "Diabetes Screening Package",
                    "code": "PKG-DIAB-01",
                    "description": "Essential tests for diabetic patients",
                    "price": "899.00",
                    "market_price": "1120.00",
                    "preparation_instructions": "Fast for 8 to 10 hours overnight.",
                    "test_ids": [1, 2]
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.package && res.package.id) {",
                    "    pm.collectionVariables.set('packageId', res.package.id);",
                    "}"
                ],
                example_response={
                    "success": True,
                    "message": "Health package 'Diabetes Screening Package' created successfully.",
                    "package": {
                        "id": 2,
                        "name": "Diabetes Screening Package",
                        "price": "899.00",
                        "tests_count": 2
                    }
                },
                status_code=201
            ),
            make_req(
                name="2.8 Packages - Get Package Details",
                method="GET",
                path="/api/packages/{{packageId}}/",
                example_response={
                    "success": True,
                    "package": {
                        "id": 1,
                        "name": "Comprehensive Full Body Checkup",
                        "price": "1499.00",
                        "tests_count": 5
                    }
                },
                status_code=200
            )
        ]
    },
    {
        "name": "Flow 3 - Diagnostic Partner Discovery & Availability",
        "description": "Steps 5 to 7: Select partner centre, verify pincode serviceability, and inspect available date & time slots.",
        "item": [
            make_req(
                name="3.1 Partners - Browse Centres (City & Service Filter)",
                method="GET",
                path="/api/partners/",
                query={"city": "Hyderabad", "home_collection": "true"},
                example_response=[
                    {
                        "id": 1,
                        "centre_name": "Apollo Diagnostics Madhapur",
                        "city": "Hyderabad",
                        "address": "Plot 42, Hitech City Main Road",
                        "pincode": "500081",
                        "phone_number": "+914012345678",
                        "rating": "4.8",
                        "offers_home_collection": True,
                        "offers_centre_visit": True,
                        "home_collection_charge": "50.00"
                    }
                ],
                status_code=200
            ),
            make_req(
                name="3.2 Partners - Get Partner Details & Custom Prices",
                method="GET",
                path="/api/partners/{{partnerId}}/",
                example_response={
                    "id": 1,
                    "centre_name": "Apollo Diagnostics Madhapur",
                    "city": "Hyderabad",
                    "offers_home_collection": True,
                    "test_pricings": [
                        {"test_id": 1, "test_name": "Complete Blood Count (CBC)", "price": "270.00", "is_available": True}
                    ]
                },
                status_code=200
            ),
            make_req(
                name="3.3 Serviceability - Check Home Collection Pincode",
                method="POST",
                path="/api/partners/check-serviceability/",
                body={
                    "pincode": "500081",
                    "city": "Hyderabad",
                    "partner_id": 1
                },
                example_response={
                    "serviceable": True,
                    "partner_id": 1,
                    "partner_name": "Apollo Diagnostics Madhapur",
                    "pincode": "500081",
                    "city": "Hyderabad",
                    "home_collection_fee": "50.00",
                    "min_order_amount": "300.00"
                },
                status_code=200
            ),
            make_req(
                name="3.4 Slots - View Available Date & Time Slots",
                method="GET",
                path="/api/partners/{{partnerId}}/slots/",
                query={"date": "2026-11-15", "mode": "HOME_COLLECTION"},
                example_response=[
                    {
                        "id": 12,
                        "slot_date": "2026-11-15",
                        "start_time": "07:30:00",
                        "end_time": "08:30:00",
                        "slot_type": "HOME_COLLECTION",
                        "available_capacity": 5,
                        "is_available": True
                    },
                    {
                        "id": 13,
                        "slot_date": "2026-11-15",
                        "start_time": "08:30:00",
                        "end_time": "09:30:00",
                        "slot_type": "HOME_COLLECTION",
                        "available_capacity": 4,
                        "is_available": True
                    }
                ],
                status_code=200
            )
        ]
    },
    {
        "name": "Flow 4 - Booking Review & 30% Advance Payment Flow",
        "description": "Steps 8 to 9: Review 30%/70% breakdown, create booking, pay exactly 30% advance, and submit to partner.",
        "item": [
            make_req(
                name="4.1 Review - Price Estimate (30% Advance & 70% Balance)",
                method="POST",
                path="/api/bookings/price-estimate/",
                body={
                    "partner_id": 1,
                    "booking_mode": "HOME_COLLECTION",
                    "test_ids": [1],
                    "package_ids": [1]
                },
                example_response={
                    "success": True,
                    "estimate": {
                        "items_total": "1619.00",
                        "home_collection_fee": "50.00",
                        "total_amount": "1669.00",
                        "advance_percentage": "30.00",
                        "advance_amount": "500.70",
                        "balance_amount": "1168.30"
                    }
                },
                status_code=200
            ),
            make_req(
                name="4.2 Booking - Create Booking Request",
                method="POST",
                path="/api/bookings/",
                auth=True,
                body={
                    "patient_type": "FAMILY_MEMBER",
                    "family_member_id": 1,
                    "partner_id": 1,
                    "booking_mode": "HOME_COLLECTION",
                    "appointment_date": "2026-11-15",
                    "slot_id": 12,
                    "appointment_time": "07:30:00",
                    "collection_address": "Flat 302, Green Meadows, Madhapur",
                    "collection_city": "Hyderabad",
                    "collection_pincode": "500081",
                    "contact_phone": "+919876543210",
                    "test_ids": [1],
                    "package_ids": [1],
                    "notes": "Patient is diabetic, needs morning collection."
                },
                test_script=[
                    "var res = pm.response.json();",
                    "if (res.booking && res.booking.id) {",
                    "    pm.collectionVariables.set('bookingId', res.booking.id);",
                    "    console.log('Saved bookingId to collection variable: ' + res.booking.id);",
                    "}"
                ],
                example_response={
                    "success": True,
                    "message": "Booking created. Please pay the 30% advance of ₹500.70 to submit your request to the diagnostic partner.",
                    "booking": {
                        "id": 1,
                        "booking_code": "BK-20261110-8A7F1",
                        "booking_status": "PENDING_PAYMENT",
                        "payment_status": "UNPAID",
                        "patient_display_name": "Sunita Sharma",
                        "partner_name": "Apollo Diagnostics Madhapur",
                        "total_amount": "1669.00",
                        "advance_amount": "500.70",
                        "balance_amount": "1168.30",
                        "advance_paid": "0.00",
                        "balance_paid": "0.00"
                    }
                },
                status_code=201
            ),
            make_req(
                name="4.3 Booking - Get Details & Outstanding Balance",
                method="GET",
                path="/api/bookings/{{bookingId}}/",
                auth=True,
                example_response={
                    "id": 1,
                    "booking_code": "BK-20261110-8A7F1",
                    "booking_status": "PENDING_PAYMENT",
                    "payment_status": "UNPAID",
                    "total_amount": "1669.00",
                    "advance_amount": "500.70",
                    "balance_amount": "1168.30"
                },
                status_code=200
            ),
            make_req(
                name="4.4 Payment - Pay 30% Advance (Gateway Simulation)",
                method="POST",
                path="/api/bookings/{{bookingId}}/pay-advance/",
                auth=True,
                body={
                    "payment_mode": "UPI",
                    "transaction_id": "TXN_ADV_992817203"
                },
                example_response={
                    "success": True,
                    "message": "Advance payment of ₹500.70 received. Booking is now awaiting partner confirmation.",
                    "booking": {
                        "id": 1,
                        "booking_code": "BK-20261110-8A7F1",
                        "booking_status": "AWAITING_PARTNER_CONFIRMATION",
                        "payment_status": "ADVANCE_PAID",
                        "total_amount": "1669.00",
                        "advance_paid": "500.70",
                        "balance_amount": "1168.30"
                    },
                    "payment": {
                        "id": 1,
                        "payment_type": "ADVANCE_30",
                        "amount": "500.70",
                        "payment_mode": "UPI",
                        "payment_status": "SUCCESS",
                        "transaction_id": "TXN_ADV_992817203"
                    }
                },
                status_code=200
            ),
            make_req(
                name="4.5 Booking - Cancel Booking (User-Initiated)",
                method="POST",
                path="/api/bookings/{{bookingId}}/cancel/",
                auth=True,
                body={
                    "reason": "Change of schedule"
                },
                example_response={
                    "success": True,
                    "message": "Booking cancelled successfully. Refund initiated.",
                    "booking_status": "CANCELLED",
                    "payment_status": "REFUNDED"
                },
                status_code=200
            )
        ]
    },
    {
        "name": "Flow 5 - Diagnostic Partner Dashboard & Lifecycle",
        "description": "Steps 10 to 11: Partner receives incoming bookings, accepts/rejects with auto-refund, and updates service lifecycle.",
        "item": [
            make_req(
                name="5.1 Partner - Dashboard Summary & KPIs",
                method="GET",
                path="/api/partner/dashboard/summary/",
                auth=True,
                example_response={
                    "partner_name": "Apollo Diagnostics Madhapur",
                    "metrics": {
                        "pending_confirmation_count": 1,
                        "confirmed_count": 5,
                        "sample_collection_count": 3,
                        "completed_count": 28,
                        "total_advance_collected": "14500.00",
                        "outstanding_balance": "4200.00"
                    }
                },
                status_code=200
            ),
            make_req(
                name="5.2 Partner - Incoming Booking Requests",
                method="GET",
                path="/api/partner/bookings/",
                auth=True,
                query={"status": "AWAITING_PARTNER_CONFIRMATION"},
                example_response=[
                    {
                        "id": 1,
                        "booking_code": "BK-20261110-8A7F1",
                        "patient_name": "Sunita Sharma",
                        "contact_phone": "+919876543210",
                        "booking_mode": "HOME_COLLECTION",
                        "appointment_date": "2026-11-15",
                        "appointment_time": "07:30:00",
                        "booking_status": "AWAITING_PARTNER_CONFIRMATION",
                        "payment_status": "ADVANCE_PAID",
                        "advance_paid": "500.70"
                    }
                ],
                status_code=200
            ),
            make_req(
                name="5.3 Partner - Accept Booking (Confirmed)",
                method="POST",
                path="/api/partner/bookings/{{bookingId}}/accept/",
                auth=True,
                body={},
                example_response={
                    "success": True,
                    "message": "Booking BK-20261110-8A7F1 has been accepted and confirmed.",
                    "booking": {
                        "id": 1,
                        "booking_status": "CONFIRMED"
                    }
                },
                status_code=200
            ),
            make_req(
                name="5.4 Partner - Update Status to Sample Collection Visit",
                method="POST",
                path="/api/partner/bookings/{{bookingId}}/update-status/",
                auth=True,
                body={
                    "status": "SAMPLE_COLLECTION",
                    "notes": "Phlebotomist Rajesh has been dispatched to collection address."
                },
                example_response={
                    "success": True,
                    "message": "Booking status updated to SAMPLE_COLLECTION.",
                    "booking": {
                        "id": 1,
                        "booking_status": "SAMPLE_COLLECTION"
                    }
                },
                status_code=200
            ),
            make_req(
                name="5.5 Partner - Mark Service Completed (Triggers 70% Alert)",
                method="POST",
                path="/api/partner/bookings/{{bookingId}}/update-status/",
                auth=True,
                body={
                    "status": "TEST_COMPLETED",
                    "notes": "Sample received and laboratory processing completed."
                },
                example_response={
                    "success": True,
                    "message": "Booking status updated to TEST_COMPLETED. Patient notified to pay remaining balance.",
                    "booking": {
                        "id": 1,
                        "booking_status": "TEST_COMPLETED"
                    }
                },
                status_code=200
            ),
            make_req(
                name="5.6 Partner - Reject Booking (Auto-Refunding Advance)",
                method="POST",
                path="/api/partner/bookings/{{bookingId}}/reject/",
                auth=True,
                body={
                    "rejection_reason": "No phlebotomist available for requested area at this slot."
                },
                example_response={
                    "success": True,
                    "message": "Booking rejected. 30% advance payment refund initiated.",
                    "booking": {
                        "id": 1,
                        "booking_status": "CANCELLED",
                        "payment_status": "REFUNDED",
                        "cancellation_reason": "No phlebotomist available for requested area at this slot."
                    }
                },
                status_code=200
            )
        ]
    },
    {
        "name": "Flow 6 - 70% Balance Settlement & Final Receipt",
        "description": "Step 12: Collect remaining 70% via Online Payment Gateway OR Partner Offline POS/Cash Recording.",
        "item": [
            make_req(
                name="6.1 Online Settlement - Patient Pays Remaining 70% Balance",
                method="POST",
                path="/api/bookings/{{bookingId}}/pay-balance/",
                auth=True,
                body={
                    "payment_mode": "CREDIT_CARD",
                    "transaction_id": "TXN_BAL_771829103"
                },
                example_response={
                    "success": True,
                    "message": "Final balance payment of ₹1168.30 received. Booking is fully paid.",
                    "booking": {
                        "id": 1,
                        "booking_code": "BK-20261110-8A7F1",
                        "booking_status": "TEST_COMPLETED",
                        "payment_status": "FULLY_PAID",
                        "total_amount": "1669.00",
                        "advance_paid": "500.70",
                        "balance_paid": "1168.30",
                        "balance_amount": "0.00"
                    },
                    "payment": {
                        "id": 2,
                        "payment_type": "BALANCE_70",
                        "amount": "1168.30",
                        "payment_mode": "CREDIT_CARD",
                        "payment_status": "SUCCESS",
                        "transaction_id": "TXN_BAL_771829103"
                    }
                },
                status_code=200
            ),
            make_req(
                name="6.2 Offline Settlement - Partner Records Cash / POS Collection",
                method="POST",
                path="/api/partner/bookings/{{bookingId}}/record-balance/",
                auth=True,
                body={
                    "amount": "1168.30",
                    "payment_mode": "CASH",
                    "transaction_reference": "POS-REC-994821",
                    "notes": "Collected cash at centre counter."
                },
                example_response={
                    "success": True,
                    "message": "Offline balance payment of ₹1168.30 recorded successfully.",
                    "booking": {
                        "id": 1,
                        "payment_status": "FULLY_PAID",
                        "balance_paid": "1168.30",
                        "balance_amount": "0.00"
                    }
                },
                status_code=200
            ),
            make_req(
                name="6.3 Final Summary - Booking & Payment Receipt",
                method="GET",
                path="/api/bookings/{{bookingId}}/",
                auth=True,
                example_response={
                    "id": 1,
                    "booking_code": "BK-20261110-8A7F1",
                    "booking_status": "TEST_COMPLETED",
                    "payment_status": "FULLY_PAID",
                    "total_amount": "1669.00",
                    "advance_paid": "500.70",
                    "balance_paid": "1168.30",
                    "balance_amount": "0.00",
                    "payments": [
                        {
                            "payment_type": "ADVANCE_30",
                            "amount": "500.70",
                            "payment_mode": "UPI",
                            "transaction_id": "TXN_ADV_992817203"
                        },
                        {
                            "payment_type": "BALANCE_70",
                            "amount": "1168.30",
                            "payment_mode": "CREDIT_CARD",
                            "transaction_id": "TXN_BAL_771829103"
                        }
                    ]
                },
                status_code=200
            )
        ]
    },
    {
        "name": "Flow 7 - Notifications & Admin Monitoring",
        "description": "Patient notifications and administrative KPIs, booking supervision, and partner approvals.",
        "item": [
            make_req(
                name="7.1 Notifications - View Patient Notifications",
                method="GET",
                path="/api/notifications/",
                auth=True,
                example_response=[
                    {
                        "id": 1,
                        "title": "Advance Payment Received",
                        "message": "Your booking BK-20261110-8A7F1 is awaiting partner confirmation.",
                        "notification_type": "PAYMENT_SUCCESS",
                        "is_read": False,
                        "created_at": "2026-11-10T12:00:00Z"
                    },
                    {
                        "id": 2,
                        "title": "Booking Confirmed",
                        "message": "Apollo Diagnostics Madhapur has confirmed your appointment.",
                        "notification_type": "BOOKING_CONFIRMED",
                        "is_read": False,
                        "created_at": "2026-11-10T12:05:00Z"
                    }
                ],
                status_code=200
            ),
            make_req(
                name="7.2 Notifications - Mark Read",
                method="POST",
                path="/api/notifications/1/mark-read/",
                auth=True,
                body={},
                example_response={
                    "success": True,
                    "message": "Notification marked as read."
                },
                status_code=200
            ),
            make_req(
                name="7.3 Admin - System Dashboard Metrics & Revenue",
                method="GET",
                path="/api/admin/dashboard/metrics/",
                auth=True,
                example_response={
                    "total_users": 154,
                    "total_partners": 12,
                    "active_partners": 10,
                    "total_bookings": 320,
                    "total_booking_value": "450200.00",
                    "advance_collected": "135060.00",
                    "balance_outstanding": "315140.00",
                    "pending_partner_approvals": 2
                },
                status_code=200
            ),
            make_req(
                name="7.4 Admin - Monitor All Platform Bookings",
                method="GET",
                path="/api/admin/bookings/",
                auth=True,
                query={"status": "CONFIRMED"},
                example_response=[
                    {
                        "id": 1,
                        "booking_code": "BK-20261110-8A7F1",
                        "user_phone": "+919876543210",
                        "patient_name": "Sunita Sharma",
                        "partner_name": "Apollo Diagnostics Madhapur",
                        "booking_status": "CONFIRMED",
                        "payment_status": "ADVANCE_PAID",
                        "total_amount": "1669.00",
                        "advance_amount": "500.70",
                        "balance_amount": "1168.30"
                    }
                ],
                status_code=200
            ),
            make_req(
                name="7.5 Admin - Approve / Activate Diagnostic Partner",
                method="POST",
                path="/api/admin/partners/{{partnerId}}/approve/",
                auth=True,
                body={
                    "is_verified": True,
                    "is_active": True
                },
                example_response={
                    "success": True,
                    "message": "Partner onboarding status updated.",
                    "partner": {
                        "id": 1,
                        "centre_name": "Apollo Diagnostics Madhapur",
                        "is_verified": True,
                        "is_active": True
                    }
                },
                status_code=200
            )
        ]
    }
]

collection = {
    "info": {
        "_postman_id": str(uuid.uuid4()),
        "name": "Healthcare Diagnostics Platform API (Flow-Wise)",
        "description": "Comprehensive Postman Collection for the Healthcare Diagnostics Backend, including user registration with phone OTP, diagnostic test catalogue, partner slots, 30% advance / 70% balance payments, partner lifecycle, and admin metrics.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    },
    "variable": [
        {"key": "baseUrl", "value": "http://127.0.0.1:8000", "type": "string"},
        {"key": "accessToken", "value": "", "type": "string"},
        {"key": "refreshToken", "value": "", "type": "string"},
        {"key": "bookingId", "value": "1", "type": "string"},
        {"key": "partnerId", "value": "1", "type": "string"},
        {"key": "testId", "value": "1", "type": "string"},
        {"key": "packageId", "value": "1", "type": "string"},
        {"key": "familyMemberId", "value": "1", "type": "string"}
    ],
    "item": flows
}

output_path = "Healthcare_Diagnostics_API.postman_collection.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(collection, f, indent=2)

print(f"Postman collection successfully generated at: {output_path}")
print(f"Total flows: {len(flows)}")
total_reqs = sum(len(f['item']) for f in flows)
print(f"Total requests: {total_reqs}")


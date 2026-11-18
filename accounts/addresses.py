"""
Saved addresses (address book) + geocoding.

Address book rules
------------------
* First address a user saves becomes the default automatically.
* Exactly one default (DB constraint); switching default is atomic.
* Deleting the default promotes the most recently used remaining address.
* The default is mirrored to User.address / city / pincode so legacy screens and
  profile-completion checks keep working.
* One HOME + one WORK per user (DB constraint); unlimited OTHER, capped at
  UserAddress.MAX_PER_USER in total.

Geocoding
---------
Server-side proxy so the browser never talks to a geocoder directly: one place to
cache, rate-limit and swap providers. Uses Google Geocoding when
GOOGLE_MAPS_API_KEY is configured, otherwise OpenStreetMap Nominatim (free; its
policy allows ~1 request/second, which the cache + gate below respect).
"""
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field as dc_field
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import UserAddress

logger = logging.getLogger(__name__)

PINCODE_RE = re.compile(r'^[1-9]\d{5}$')
# Generous India bounding box (incl. A&N and Lakshadweep).
INDIA_BOUNDS = (6.0, 37.6, 68.0, 97.6)  # lat_min, lat_max, lng_min, lng_max


@dataclass
class AddressError(Exception):
    message: str
    code: str = 'invalid'
    status: int = 400
    field: str = None
    extra: dict = dc_field(default_factory=dict)

    def __str__(self):
        return self.message


# ───────────────────────── coordinates ─────────────────────────

def quantize(v):
    """6 decimal places ≈ 11 cm — more precision than any phone GPS."""
    return Decimal(str(v)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)


def parse_coords(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise AddressError('Invalid map location.', 'invalid_location', field='latitude')
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise AddressError('Invalid map location.', 'invalid_location', field='latitude')
    return lat, lng


def in_service_country(lat, lng):
    a, b, c, d = INDIA_BOUNDS
    return a <= lat <= b and c <= lng <= d


# ───────────────────────── address book ─────────────────────────

def _mirror_default_to_profile(user, address):
    """Keeps User.address/city/pincode in sync with the default address."""
    if address is None:
        return
    line = ', '.join(p for p in [address.house, address.area, address.landmark and f"Near {address.landmark}"] if p)
    type(user).objects.filter(pk=user.pk).update(address=line, city=address.city, pincode=address.pincode)
    user.address, user.city, user.pincode = line, address.city, address.pincode


def _lock_user(user):
    """Serializes address-book writes per user (row lock on Postgres)."""
    type(user).objects.select_for_update().filter(pk=user.pk).first()


def _label_conflict(user, label, exclude_id=None):
    if label not in UserAddress.UNIQUE_LABELS:
        return None
    qs = UserAddress.objects.filter(user=user, label=label)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.first()


def save_address(user, data: dict, instance: UserAddress = None, replace_label=False):
    """
    Create or update an address. `data` is validated serializer data.
    If HOME/WORK is already taken: raise `label_taken` unless replace_label=True,
    in which case the other one is relabelled OTHER (keeps its data, like Swiggy).
    """
    with transaction.atomic():
        _lock_user(user)

        if instance is None and UserAddress.objects.filter(user=user).count() >= UserAddress.MAX_PER_USER:
            raise AddressError(
                f'You can save up to {UserAddress.MAX_PER_USER} addresses. Remove one to add another.', 'limit_reached',
            )

        label = data.get('label', instance.label if instance else 'HOME')
        clash = _label_conflict(user, label, exclude_id=instance.pk if instance else None)
        if clash:
            if not replace_label:
                raise AddressError(
                    f'You already have a {clash.get_label_display()} address. Replace it, or save this one as Other.',
                    'label_taken', status=409, field='label', extra={'existing_id': clash.pk},
                )
            clash.label = 'OTHER'
            clash.custom_label = clash.custom_label or f'Old {clash.get_label_display().lower()}'
            clash.save(update_fields=['label', 'custom_label', 'updated_at'])

        make_default = data.pop('is_default', None)
        obj = instance or UserAddress(user=user)
        for k, v in data.items():
            setattr(obj, k, v)
        if obj.label != 'OTHER':
            obj.custom_label = ''

        has_any = UserAddress.objects.filter(user=user).exclude(pk=obj.pk).exists()
        want_default = bool(make_default) or not has_any or (instance is not None and instance.is_default and make_default is not False)
        if want_default:
            UserAddress.objects.filter(user=user, is_default=True).exclude(pk=obj.pk).update(is_default=False)
        elif make_default is False and instance is not None and instance.is_default:
            # Can't un-default without choosing another one.
            raise AddressError('Choose another address as default first.', 'default_required', field='is_default')
        obj.is_default = want_default

        try:
            obj.save()
        except IntegrityError:
            raise AddressError('That address conflicts with another one. Please retry.', 'conflict', status=409)

        if obj.is_default:
            _mirror_default_to_profile(user, obj)
    return obj


def set_default_address(user, address_id):
    with transaction.atomic():
        _lock_user(user)
        obj = UserAddress.objects.filter(user=user, pk=address_id).first()
        if not obj:
            raise AddressError('Address not found.', 'not_found', status=404)
        if not obj.is_default:
            UserAddress.objects.filter(user=user, is_default=True).update(is_default=False)
            obj.is_default = True
            obj.save(update_fields=['is_default', 'updated_at'])
        _mirror_default_to_profile(user, obj)
    return obj


def delete_address(user, address_id):
    with transaction.atomic():
        _lock_user(user)
        obj = UserAddress.objects.filter(user=user, pk=address_id).first()
        if not obj:
            raise AddressError('Address not found.', 'not_found', status=404)
        was_default = obj.is_default
        obj.delete()
        promoted = None
        if was_default:
            promoted = UserAddress.objects.filter(user=user).order_by(
                F('last_used_at').desc(nulls_last=True), '-updated_at',
            ).first()
            if promoted:
                promoted.is_default = True
                promoted.save(update_fields=['is_default', 'updated_at'])
                _mirror_default_to_profile(user, promoted)
    return promoted


def touch_address(address):
    UserAddress.objects.filter(pk=address.pk).update(last_used_at=timezone.now())


# ───────────────────────── geocoding ─────────────────────────

NOMINATIM = 'https://nominatim.openstreetmap.org'
GOOGLE_GEOCODE = 'https://maps.googleapis.com/maps/api/geocode/json'
_gate_lock = threading.Lock()
_last_call = [0.0]


def _user_agent():
    return getattr(settings, 'GEOCODER_USER_AGENT', '1MinDoc-Diagnostics/1.0 (address lookup)')


def _http_get(url, params):
    # Nominatim: max 1 req/s per application. Gate within this process.
    if 'nominatim' in url:
        with _gate_lock:
            wait = 1.0 - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.monotonic()
    try:
        r = requests.get(url, params=params, timeout=6, headers={
            'User-Agent': _user_agent(), 'Accept-Language': 'en-IN,en',
        })
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Geocoder request failed: %s', exc)
        raise AddressError('Location service is unavailable right now. Enter the address manually.', 'geocoder_unavailable', status=503)


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ''


def _clean_pin(v):
    v = re.sub(r'\D', '', str(v or ''))
    return v if PINCODE_RE.match(v) else ''


def _from_nominatim(item):
    a = item.get('address') or {}
    area_parts = [
        _first(a, 'house_number') and f"{a.get('house_number')}",
        _first(a, 'road', 'pedestrian', 'footway'),
        _first(a, 'neighbourhood', 'residential', 'quarter'),
        _first(a, 'suburb', 'city_district', 'hamlet'),
    ]
    area = ', '.join(dict.fromkeys(p for p in area_parts if p))  # ordered de-dupe
    city = _first(a, 'city', 'town', 'village', 'municipality', 'county', 'state_district')
    title = _first(a, 'amenity', 'building', 'shop', 'office', 'neighbourhood', 'suburb', 'road', 'village', 'town', 'city') or city
    return {
        'title': title,
        'area': area,
        'city': city,
        'district': _first(a, 'state_district', 'county'),
        'state': a.get('state', ''),
        'pincode': _clean_pin(a.get('postcode')),
        'country': (a.get('country_code') or '').upper(),
        'formatted': item.get('display_name', ''),
        'latitude': float(item['lat']),
        'longitude': float(item['lon']),
        'place_id': f"osm:{item.get('osm_type', '')}{item.get('osm_id', '')}",
    }


def _from_google(item):
    comps = {}
    for c in item.get('address_components', []):
        for t in c.get('types', []):
            comps.setdefault(t, c.get('long_name'))
    area = ', '.join(dict.fromkeys(p for p in [
        comps.get('street_number'), comps.get('route'), comps.get('sublocality_level_2'),
        comps.get('sublocality_level_1') or comps.get('neighborhood'),
    ] if p))
    loc = item['geometry']['location']
    city = comps.get('locality') or comps.get('administrative_area_level_3') or comps.get('administrative_area_level_2', '')
    return {
        'title': comps.get('premise') or comps.get('sublocality_level_1') or comps.get('route') or city,
        'area': area,
        'city': city,
        'district': comps.get('administrative_area_level_2', ''),
        'state': comps.get('administrative_area_level_1', ''),
        'pincode': _clean_pin(comps.get('postal_code')),
        'country': '',
        'formatted': item.get('formatted_address', ''),
        'latitude': loc['lat'],
        'longitude': loc['lng'],
        'place_id': f"g:{item.get('place_id', '')}",
    }


def _google_key():
    return getattr(settings, 'GOOGLE_MAPS_API_KEY', '') or ''


def reverse_geocode(lat, lng):
    lat, lng = parse_coords(lat, lng)
    # ~1 m grid: neighbouring drags share a cache entry.
    key = f'geo:rev:{round(lat, 5)}:{round(lng, 5)}'
    hit = cache.get(key)
    if hit:
        return hit

    if _google_key():
        data = _http_get(GOOGLE_GEOCODE, {'latlng': f'{lat},{lng}', 'key': _google_key(), 'region': 'in'})
        results = data.get('results') or []
        result = _from_google(results[0]) if results else None
    else:
        data = _http_get(f'{NOMINATIM}/reverse', {
            'format': 'jsonv2', 'lat': lat, 'lon': lng, 'zoom': 18, 'addressdetails': 1,
        })
        result = _from_nominatim(data) if data and not data.get('error') else None

    if not result:
        raise AddressError('We couldn’t find an address here. Move the pin a little.', 'not_found', status=404)
    # Keep the user's exact pin, not the geocoder's snapped point.
    result.update(latitude=lat, longitude=lng, serviceable_country=in_service_country(lat, lng))
    cache.set(key, result, 60 * 60 * 24)
    return result


def search_places(query, near=None, limit=6):
    q = re.sub(r'\s+', ' ', (query or '')).strip()
    if len(q) < 3:
        return []
    bias = ''
    if near:
        try:
            nlat, nlng = parse_coords(*near)
            bias = f'{round(nlat, 1)}:{round(nlng, 1)}'
        except AddressError:
            near = None
    digest = hashlib.sha1(f'{q.lower()}|{bias}'.encode()).hexdigest()
    key = f'geo:search:{digest}'
    hit = cache.get(key)
    if hit is not None:
        return hit

    if _google_key():
        params = {'address': q, 'key': _google_key(), 'region': 'in', 'components': 'country:IN'}
        data = _http_get(GOOGLE_GEOCODE, params)
        results = [_from_google(r) for r in (data.get('results') or [])[:limit]]
    else:
        params = {'format': 'jsonv2', 'q': q, 'addressdetails': 1, 'countrycodes': 'in', 'limit': limit}
        if near:
            nlat, nlng = near
            d = 0.5  # ~55 km box: prefer, don't restrict, nearby matches
            params.update(viewbox=f'{nlng - d},{nlat + d},{nlng + d},{nlat - d}', bounded=0)
        data = _http_get(f'{NOMINATIM}/search', params)
        results = [_from_nominatim(r) for r in (data or [])]

    cache.set(key, results, 60 * 60)
    return results

from __future__ import annotations

from devicelab.domain.models import DeviceRequirement, DeviceView
from devicelab.domain.versions import compare_versions


def matches(requirement: DeviceRequirement, device: DeviceView) -> bool:
    """Capability match only. Health and availability are the scheduler's concern."""
    if requirement.site and device.site != requirement.site:
        return False
    if requirement.region and device.region != requirement.region:
        return False
    if requirement.model_family and device.model_family != requirement.model_family:
        return False
    if requirement.min_ios and compare_versions(device.ios_version, requirement.min_ios) < 0:
        return False
    if requirement.max_ios and compare_versions(device.ios_version, requirement.max_ios) > 0:
        return False
    return requirement.labels <= device.labels

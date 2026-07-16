from video_editor.hardware import HardwareCapabilities, choose_backend, supported_backends_for_platform
from video_editor.models import HardwareBackend, VideoCodec


def test_unavailable_requested_backend_falls_back_to_cpu():
    capabilities = HardwareCapabilities()

    assert choose_backend(capabilities, HardwareBackend.VAAPI) == HardwareBackend.CPU
    assert choose_backend(capabilities, HardwareBackend.NVENC) == HardwareBackend.CPU


def test_platform_backend_options_always_include_auto_and_cpu():
    backends = supported_backends_for_platform()

    assert backends[0] == HardwareBackend.AUTO
    assert HardwareBackend.CPU in backends


def test_backend_selection_is_specific_to_requested_codec():
    capabilities = HardwareCapabilities(
        encoders=["h264_nvenc", "libsvtav1"],
        has_nvenc=True,
    )

    assert capabilities.supports(HardwareBackend.NVENC, VideoCodec.H264)
    assert not capabilities.supports(HardwareBackend.NVENC, VideoCodec.AV1)
    assert capabilities.supports(HardwareBackend.CPU, VideoCodec.AV1)
    assert choose_backend(capabilities, HardwareBackend.AUTO, VideoCodec.H264) == HardwareBackend.NVENC
    assert choose_backend(capabilities, HardwareBackend.NVENC, VideoCodec.AV1) == HardwareBackend.CPU

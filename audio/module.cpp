// SPDX-License-Identifier: AGPL-3.0-only
#include <algorithm>
#include <cmath>

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include "audio.h"
#include "engine.h"
#include "output.h"

namespace py = pybind11;

using namioto::audio::Engine;
using namioto::audio::Output;
using namioto::audio::Stretcher;

namespace {

py::array_t<float> process(Stretcher& self, py::array_t<float, py::array::c_style | py::array::forcecast> input,
                           double speed) {
    const auto info = input.request();
    const auto count = static_cast<int>(info.size);
    const auto output_count = count > 0 ? std::max(1, static_cast<int>(std::lround(count / speed))) : 0;
    py::array_t<float> output(output_count);
    if (output_count > 0) {
        self.process(static_cast<const float*>(info.ptr), count, output.mutable_data(), output_count);
    }
    return output;
}

}  // namespace

PYBIND11_MODULE(_audio, m) {
    m.doc() = "The native audio backend: Signalsmith Stretch and miniaudio.";
    m.attr("__version__") = "0.1.0";

    m.def("stretch_version", &namioto::audio::stretch_version);
    m.def("miniaudio_version", &namioto::audio::miniaudio_version);

    py::class_<Stretcher>(m, "Stretcher")
        .def(py::init<int, double, double, bool>(), py::arg("sample_rate"), py::arg("block_ms") = 120.0,
             py::arg("overlap") = 4.0, py::arg("split_computation") = false)
        .def_property_readonly("input_latency", &Stretcher::input_latency)
        .def_property_readonly("output_latency", &Stretcher::output_latency)
        .def("reset", &Stretcher::reset)
        .def("process", &process, py::arg("input"), py::arg("speed") = 1.0)
        .def("flush", [](Stretcher& self, int count) {
            py::array_t<float> output(std::max(0, count));
            if (count > 0) {
                self.flush(output.mutable_data(), count);
            }
            return output;
        }, py::arg("count"));

    py::class_<Engine>(m, "Engine")
        .def(py::init<int>(), py::arg("sample_rate"))
        .def("load", [](Engine& self, py::array_t<float, py::array::c_style | py::array::forcecast> samples) {
            const auto info = samples.request();
            self.load(static_cast<const float*>(info.ptr), static_cast<int64_t>(info.size));
        }, py::arg("samples"))
        .def("unload", &Engine::unload)
        .def_property_readonly("loaded", &Engine::loaded)
        .def_property_readonly("duration", &Engine::duration)
        .def_property_readonly("position", &Engine::position)
        .def_property_readonly("playing", &Engine::playing)
        .def_property_readonly("finished", &Engine::finished)
        .def("play", &Engine::play, py::arg("seconds") = 0.0)
        .def("pause", &Engine::pause)
        .def("seek", &Engine::seek, py::arg("seconds"))
        .def_property("speed", &Engine::speed, &Engine::set_speed)
        .def_property("gain", &Engine::gain, &Engine::set_gain)
        .def("submit_buffer", [](Engine& self, py::array_t<float, py::array::c_style | py::array::forcecast> data,
                                  double start_seconds, double gain, int id) {
            const auto info = data.request();
            self.submit_buffer(static_cast<const float*>(info.ptr), static_cast<int64_t>(info.size), start_seconds, gain,
                               id);
        }, py::arg("data"), py::arg("start_seconds"), py::arg("gain") = 1.0, py::arg("id") = 0)
        .def("clear_buffers", &Engine::clear_buffers)
        .def("pull", [](Engine& self, int frames) {
            py::array_t<float> output(std::max(0, frames));
            if (frames > 0) {
                self.pull(output.mutable_data(), frames);
            }
            return output;
        }, py::arg("frames"));

    py::class_<Output>(m, "Output")
        .def(py::init<int, int>(), py::arg("sample_rate"), py::arg("block_frames") = 1024)
        .def("open", &Output::open, py::arg("null_backend") = false)
        .def("close", &Output::close)
        .def_property_readonly("is_open", &Output::is_open)
        .def("error", &Output::error)
        .def("load", [](Output& self, py::array_t<float, py::array::c_style | py::array::forcecast> samples) {
            const auto info = samples.request();
            self.load(static_cast<const float*>(info.ptr), static_cast<int64_t>(info.size));
        }, py::arg("samples"))
        .def("unload", &Output::unload)
        .def_property_readonly("loaded", &Output::loaded)
        .def_property_readonly("duration", &Output::duration)
        .def_property_readonly("position", &Output::position)
        .def_property_readonly("playing", &Output::playing)
        .def_property_readonly("finished", &Output::finished)
        .def("play", &Output::play, py::arg("seconds") = 0.0)
        .def("pause", &Output::pause)
        .def("seek", &Output::seek, py::arg("seconds"))
        .def_property("speed", &Output::speed, &Output::set_speed)
        .def_property("gain", &Output::gain, &Output::set_gain)
        .def("submit_buffer", [](Output& self, py::array_t<float, py::array::c_style | py::array::forcecast> data,
                                  double start_seconds, double gain, int id) {
            const auto info = data.request();
            self.submit_buffer(static_cast<const float*>(info.ptr), static_cast<int64_t>(info.size), start_seconds, gain,
                               id);
        }, py::arg("data"), py::arg("start_seconds"), py::arg("gain") = 1.0, py::arg("id") = 0)
        .def("clear_buffers", &Output::clear_buffers);
}

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct Vec3 {
    double x;
    double y;
    double z;
};

Vec3 add(Vec3 a, Vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
Vec3 sub(Vec3 a, Vec3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
Vec3 mul(Vec3 a, double value) { return {a.x * value, a.y * value, a.z * value}; }
double dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
Vec3 cross(Vec3 a, Vec3 b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}

class Builder {
public:
    Builder(const double* triangles, std::size_t count, int max_depth)
        : triangles_(triangles), count_(count), max_depth_(max_depth) {}

    void run() {
        std::vector<std::size_t> root;
        root.reserve(count_);
        for (std::size_t triangle = 0; triangle < count_; ++triangle) {
            if (intersects(triangle, {0.0, 0.0, 0.0}, 0.5)) {
                root.push_back(triangle);
            }
        }
        if (root.empty()) {
            masks.push_back(0);
            spans.push_back(1);
            return;
        }
        visit(root, {0.0, 0.0, 0.0}, 0.5, 0);
    }

    std::vector<std::uint8_t> masks;
    std::vector<std::uint32_t> spans;
    std::uint64_t leaf_count = 0;

private:
    const double* triangle(std::size_t index) const { return triangles_ + index * 9; }

    Vec3 vertex(const double* values, int index) const {
        const double* value = values + index * 3;
        return {value[0], value[1], value[2]};
    }

    bool axis_separates(const std::array<Vec3, 3>& vertices, Vec3 axis, double half, double tolerance) const {
        if (dot(axis, axis) <= 1e-30) {
            return false;
        }
        double minimum = dot(vertices[0], axis);
        double maximum = minimum;
        for (int index = 1; index < 3; ++index) {
            const double projection = dot(vertices[index], axis);
            minimum = std::min(minimum, projection);
            maximum = std::max(maximum, projection);
        }
        const double radius = half * (std::abs(axis.x) + std::abs(axis.y) + std::abs(axis.z));
        return minimum > radius + tolerance || maximum < -radius - tolerance;
    }

    bool intersects(std::size_t index, Vec3 center, double half) const {
        const double* values = triangle(index);
        std::array<Vec3, 3> vertices = {
            sub(vertex(values, 0), center),
            sub(vertex(values, 1), center),
            sub(vertex(values, 2), center),
        };
        const double tolerance = std::max(half * 1e-10, 1e-14);
        for (int axis = 0; axis < 3; ++axis) {
            double minimum = axis == 0 ? vertices[0].x : axis == 1 ? vertices[0].y : vertices[0].z;
            double maximum = minimum;
            for (int vertex_index = 1; vertex_index < 3; ++vertex_index) {
                const double value = axis == 0 ? vertices[vertex_index].x
                    : axis == 1 ? vertices[vertex_index].y : vertices[vertex_index].z;
                minimum = std::min(minimum, value);
                maximum = std::max(maximum, value);
            }
            if (minimum > half + tolerance || maximum < -half - tolerance) {
                return false;
            }
        }
        const std::array<Vec3, 3> edges = {
            sub(vertex(values, 1), vertex(values, 0)),
            sub(vertex(values, 2), vertex(values, 1)),
            sub(vertex(values, 0), vertex(values, 2)),
        };
        const Vec3 normal = cross(edges[0], sub(vertex(values, 2), vertex(values, 0)));
        if (axis_separates(vertices, normal, half, tolerance)) {
            return false;
        }
        const std::array<Vec3, 3> basis = {{{1.0, 0.0, 0.0}, {0.0, 1.0, 0.0}, {0.0, 0.0, 1.0}}};
        for (const Vec3 edge : edges) {
            for (const Vec3 axis : basis) {
                if (axis_separates(vertices, cross(edge, axis), half, tolerance)) {
                    return false;
                }
            }
        }
        return true;
    }

    void visit(const std::vector<std::size_t>& indices, Vec3 center, double half, int depth) {
        if (masks.size() >= std::numeric_limits<std::uint32_t>::max()) {
            throw std::runtime_error("surface-tree node count exceeds uint32");
        }
        const std::size_t node = masks.size();
        masks.push_back(0);
        spans.push_back(0);
        const double child_half = half / 2.0;
        for (int child = 0; child < 8; ++child) {
            const Vec3 direction = {
                child & 4 ? 1.0 : -1.0,
                child & 2 ? 1.0 : -1.0,
                child & 1 ? 1.0 : -1.0,
            };
            const Vec3 child_center = add(center, mul(direction, child_half));
            std::vector<std::size_t> child_indices;
            child_indices.reserve(indices.size());
            for (const std::size_t triangle_index : indices) {
                if (intersects(triangle_index, child_center, child_half)) {
                    child_indices.push_back(triangle_index);
                }
            }
            if (child_indices.empty()) {
                continue;
            }
            masks[node] |= static_cast<std::uint8_t>(1u << child);
            if (depth + 1 == max_depth_) {
                ++leaf_count;
            } else {
                visit(child_indices, child_center, child_half, depth + 1);
            }
        }
        spans[node] = static_cast<std::uint32_t>(masks.size() - node);
    }

    const double* triangles_;
    std::size_t count_;
    int max_depth_;
};

bool double_format(const char* format) {
    return format != nullptr && (
        std::strcmp(format, "d") == 0 ||
        std::strcmp(format, "=d") == 0 ||
        std::strcmp(format, "<d") == 0
    );
}

PyObject* build(PyObject*, PyObject* args) {
    PyObject* source = nullptr;
    int max_depth = 0;
    if (!PyArg_ParseTuple(args, "Oi:build", &source, &max_depth)) {
        return nullptr;
    }
    if (max_depth < 1 || max_depth > 21) {
        PyErr_SetString(PyExc_ValueError, "max_depth must be in [1, 21]");
        return nullptr;
    }
    Py_buffer view{};
    if (PyObject_GetBuffer(source, &view, PyBUF_FORMAT | PyBUF_ND | PyBUF_STRIDES) < 0) {
        return nullptr;
    }
    const bool valid = view.ndim == 3 && view.shape[1] == 3 && view.shape[2] == 3 &&
        view.itemsize == static_cast<Py_ssize_t>(sizeof(double)) && double_format(view.format) &&
        PyBuffer_IsContiguous(&view, 'C');
    if (!valid) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "triangles must be C-contiguous float64[F,3,3]");
        return nullptr;
    }
    if (view.shape[0] <= 0) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "triangles cannot be empty");
        return nullptr;
    }

    Builder builder(static_cast<const double*>(view.buf), static_cast<std::size_t>(view.shape[0]), max_depth);
    std::string error;
    Py_BEGIN_ALLOW_THREADS
    try {
        builder.run();
    } catch (const std::exception& exc) {
        error = exc.what();
    } catch (...) {
        error = "native octree builder failed";
    }
    Py_END_ALLOW_THREADS
    PyBuffer_Release(&view);
    if (!error.empty()) {
        PyErr_SetString(PyExc_RuntimeError, error.c_str());
        return nullptr;
    }

    PyObject* mask_bytes = PyBytes_FromStringAndSize(
        reinterpret_cast<const char*>(builder.masks.data()),
        static_cast<Py_ssize_t>(builder.masks.size())
    );
    std::string span_bytes(builder.spans.size() * 4, '\0');
    for (std::size_t index = 0; index < builder.spans.size(); ++index) {
        const std::uint32_t value = builder.spans[index];
        for (int byte = 0; byte < 4; ++byte) {
            span_bytes[index * 4 + byte] = static_cast<char>((value >> (byte * 8)) & 0xff);
        }
    }
    PyObject* spans = PyBytes_FromStringAndSize(span_bytes.data(), static_cast<Py_ssize_t>(span_bytes.size()));
    if (mask_bytes == nullptr || spans == nullptr) {
        Py_XDECREF(mask_bytes);
        Py_XDECREF(spans);
        return nullptr;
    }
    return Py_BuildValue("NNK", mask_bytes, spans, static_cast<unsigned long long>(builder.leaf_count));
}

PyMethodDef methods[] = {
    {"build", build, METH_VARARGS, "Build a hierarchical conservative surface tree."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_native",
    "Native hierarchical conservative SAT builder.",
    -1,
    methods,
};

}  // namespace

PyMODINIT_FUNC PyInit__native() { return PyModule_Create(&module); }

// ==============================================================
// XSI Loader Implementation
// ==============================================================

#include "xsi_loader.h"
#include <dlfcn.h>
#include <stdexcept>
#include <cstring>
#include <iostream>

namespace Xsi {

Loader::Loader(const std::string& design_so, const std::string& kernel_so)
    : design_lib_handle_(nullptr),
      kernel_lib_handle_(nullptr),
      design_handle_(nullptr) {

    // Load design shared library
    design_lib_handle_ = dlopen(design_so.c_str(), RTLD_LAZY | RTLD_GLOBAL);
    if (!design_lib_handle_) {
        throw std::runtime_error(std::string("Failed to load design library: ") + dlerror());
    }

    // Load kernel shared library
    kernel_lib_handle_ = dlopen(kernel_so.c_str(), RTLD_LAZY | RTLD_GLOBAL);
    if (!kernel_lib_handle_) {
        dlclose(design_lib_handle_);
        throw std::runtime_error(std::string("Failed to load kernel library: ") + dlerror());
    }

    // Get function pointers from design library
    xsi_open_fp_ = (xsi_open_t)get_function_pointer(design_lib_handle_, "xsi_open");

    // Get function pointers from kernel library
    xsi_close_fp_ = (xsi_close_t)get_function_pointer(kernel_lib_handle_, "xsi_close");
    xsi_get_port_number_fp_ = (xsi_get_port_number_t)get_function_pointer(kernel_lib_handle_, "xsi_get_port_number");
    xsi_put_value_fp_ = (xsi_put_value_t)get_function_pointer(kernel_lib_handle_, "xsi_put_value");
    xsi_get_value_fp_ = (xsi_get_value_t)get_function_pointer(kernel_lib_handle_, "xsi_get_value");
    xsi_run_fp_ = (xsi_run_t)get_function_pointer(kernel_lib_handle_, "xsi_run");
    xsi_restart_fp_ = (xsi_restart_t)get_function_pointer(kernel_lib_handle_, "xsi_restart");
    xsi_get_status_fp_ = (xsi_get_status_t)get_function_pointer(kernel_lib_handle_, "xsi_get_status");
    xsi_get_error_info_fp_ = (xsi_get_error_info_t)get_function_pointer(kernel_lib_handle_, "xsi_get_error_info");
    xsi_trace_all_fp_ = (xsi_trace_all_t)get_function_pointer(kernel_lib_handle_, "xsi_trace_all");
}

Loader::~Loader() {
    if (design_handle_) {
        close();
    }
    if (kernel_lib_handle_) {
        dlclose(kernel_lib_handle_);
    }
    if (design_lib_handle_) {
        dlclose(design_lib_handle_);
    }
}

void Loader::open(p_xsi_setup_info setup_info) {
    if (design_handle_) {
        throw std::runtime_error("Design already open");
    }
    design_handle_ = xsi_open_fp_(setup_info);
    if (!design_handle_) {
        throw std::runtime_error("Failed to open design");
    }
}

void Loader::close() {
    if (design_handle_) {
        xsi_close_fp_(design_handle_);
        design_handle_ = nullptr;
    }
}

int Loader::get_port_number(const char* port_name) {
    if (!design_handle_) {
        throw std::runtime_error("Design not open");
    }
    int port_num = xsi_get_port_number_fp_(design_handle_, port_name);
    if (port_num < 0) {
        throw std::runtime_error(std::string("Port not found: ") + port_name);
    }
    return port_num;
}

void Loader::put_value(int port_number, const void* value) {
    if (!design_handle_) {
        throw std::runtime_error("Design not open");
    }
    xsi_put_value_fp_(design_handle_, port_number, const_cast<void*>(value));
}

void Loader::get_value(int port_number, void* value) {
    if (!design_handle_) {
        throw std::runtime_error("Design not open");
    }
    xsi_get_value_fp_(design_handle_, port_number, value);
}

void Loader::run(XSI_INT64 time_ticks) {
    if (!design_handle_) {
        throw std::runtime_error("Design not open");
    }
    xsi_run_fp_(design_handle_, time_ticks);
}

void Loader::restart() {
    if (!design_handle_) {
        throw std::runtime_error("Design not open");
    }
    xsi_restart_fp_(design_handle_);
}

int Loader::get_status() {
    if (!design_handle_) {
        throw std::runtime_error("Design not open");
    }
    return xsi_get_status_fp_(design_handle_);
}

const char* Loader::get_error_info() {
    if (!design_handle_) {
        return "Design not open";
    }
    return xsi_get_error_info_fp_(design_handle_);
}

void Loader::trace_all() {
    if (!design_handle_) {
        throw std::runtime_error("Design not open");
    }
    xsi_trace_all_fp_(design_handle_);
}

void* Loader::get_function_pointer(void* lib_handle, const char* func_name) {
    void* func_ptr = dlsym(lib_handle, func_name);
    if (!func_ptr) {
        throw std::runtime_error(std::string("Failed to find function: ") + func_name + " - " + dlerror());
    }
    return func_ptr;
}

} // namespace Xsi

// ==============================================================
// XSI Loader - C++ wrapper for Xilinx Simulator Interface
//
// STATUS: EXPERIMENTAL / PARTIAL
// ─────────────────────────────────────────────────────────────
// This loader powers the *XSI-driven* per-module simulation path,
// where the C++ testbench calls into a compiled XSIM design-under-
// test via the Xilinx Simulator Interface shared library.
//
// The XSI path is SEPARATE from the primary full-chip XSIM flow.
// The primary flow is file-based:
//   xml_to_sv_stimulus → .sv/.dat files → xsim → CSV →
//   algo_top_xsim_checker
//
// As of this writing only ONE XSI adapter exists in-tree:
//   plugins/omtf/verify/adapters/xsim/mod_dt_interface_adapter_xsim.cpp
//
// XSI adapter development is in-progress and not yet integrated into
// the CTest suite.  The loader is built only when XILINX_VIVADO is
// set (CMake discovers it via find_program(xsim ...)).
// ─────────────────────────────────────────────────────────────
// ==============================================================

#ifndef XSI_LOADER_H
#define XSI_LOADER_H

#include <string>
#include <cstdint>

// Forward declare XSI types
typedef void* xsiHandle;
typedef int32_t XSI_INT32;
typedef uint32_t XSI_UINT32;
typedef int64_t XSI_INT64;
typedef uint64_t XSI_UINT64;

// XSI status codes
#define xsiNormal 0
#define xsiError 1
#define xsiFatalError 2

// XSI setup info
struct s_xsi_setup_info {
    char* logFileName;
    char* wdbFileName;
    char* xsimDir;  // Runtime XSIMDIR location remapping
};
typedef struct s_xsi_setup_info *p_xsi_setup_info;

// Verilog 4-state logic value (for future use with Verilog designs)
struct s_xsi_vlog_logicval {
    XSI_UINT32 aVal;
    XSI_UINT32 bVal;
};
typedef struct s_xsi_vlog_logicval *p_xsi_vlog_logicval;

namespace Xsi {

class Loader {
public:
    // Constructor: Load XSI libraries
    Loader(const std::string& design_so, const std::string& kernel_so);

    // Destructor
    ~Loader();

    // Open the design
    void open(p_xsi_setup_info setup_info);

    // Check if design is open
    bool isopen() const { return design_handle_ != nullptr; }

    // Close the design
    void close();

    // Get port number by name
    int get_port_number(const char* port_name);

    // Set input port value
    void put_value(int port_number, const void* value);

    // Get output port value
    void get_value(int port_number, void* value);

    // Run simulation for specified time (in kernel precision units)
    void run(XSI_INT64 time_ticks);

    // Restart simulation to time 0
    void restart();

    // Get simulation status
    int get_status();

    // Get error information
    const char* get_error_info();

    // Enable waveform tracing
    void trace_all();

private:
    // Dynamic library handles
    void* design_lib_handle_;
    void* kernel_lib_handle_;

    // XSI design handle
    xsiHandle design_handle_;

    // Function pointers
    typedef xsiHandle (*xsi_open_t)(p_xsi_setup_info);
    typedef void (*xsi_close_t)(xsiHandle);
    typedef XSI_INT32 (*xsi_get_port_number_t)(xsiHandle, const char*);
    typedef void (*xsi_put_value_t)(xsiHandle, XSI_INT32, void*);
    typedef void (*xsi_get_value_t)(xsiHandle, XSI_INT32, void*);
    typedef void (*xsi_run_t)(xsiHandle, XSI_UINT64);
    typedef void (*xsi_restart_t)(xsiHandle);
    typedef XSI_INT32 (*xsi_get_status_t)(xsiHandle);
    typedef const char* (*xsi_get_error_info_t)(xsiHandle);
    typedef void (*xsi_trace_all_t)(xsiHandle);

    xsi_open_t xsi_open_fp_;
    xsi_close_t xsi_close_fp_;
    xsi_get_port_number_t xsi_get_port_number_fp_;
    xsi_put_value_t xsi_put_value_fp_;
    xsi_get_value_t xsi_get_value_fp_;
    xsi_run_t xsi_run_fp_;
    xsi_restart_t xsi_restart_fp_;
    xsi_get_status_t xsi_get_status_fp_;
    xsi_get_error_info_t xsi_get_error_info_fp_;
    xsi_trace_all_t xsi_trace_all_fp_;

    // Helper: Load function pointer
    void* get_function_pointer(void* lib_handle, const char* func_name);
};

} // namespace Xsi

#endif // XSI_LOADER_H

#ifndef RULES_H
#define RULES_H

#include <set>
#include <string>

namespace verif_fw {

struct ComparisonRules {
    bool check_phi;
    bool check_phiB;
    bool check_eta;
    bool check_quality;
    int phi_tolerance;
    int phiB_tolerance;
    int eta_tolerance;
    std::set<std::string> disabled_checks;

    ComparisonRules() : check_phi(true), check_phiB(true), check_eta(true),
                        check_quality(true), phi_tolerance(0), phiB_tolerance(0),
                        eta_tolerance(0) {}

    bool is_check_disabled(const std::string& check_name) const {
        return disabled_checks.find(check_name) != disabled_checks.end();
    }

    void disable_check(const std::string& check_name) {
        disabled_checks.insert(check_name);
    }
};

} // namespace verif_fw

#endif // RULES_H

// pn_junction.geo
// 1D silicon pn junction, after Encore's Data/Device -- but with its constants
// declared so tools can read and change them.
//
// Only DefineConstant entries with a Name attribute reach the ONELAB database.
// "lc = 0.005;" or "DefineConstant[ lc = 0.005 ];" define the variable but
// expose nothing to the outside.
//
// Coordinates in micrometers.

DefineConstant[ lc = {0.005, Name "Parameters/lc"} ];
DefineConstant[ junction = {0.5, Name "Parameters/junction"} ];
DefineConstant[ length = {1.0, Name "Parameters/length"} ];

Point(1) = {0.0, 0, 0, lc};       // cathode (n-contact)
Point(2) = {junction, 0, 0, lc};  // metallurgical junction
Point(3) = {length, 0, 0, lc};    // anode (p-contact)

Line(1) = {1, 2};  // n-region
Line(2) = {2, 3};  // p-region

Physical Point("cathode", 1) = {1};
Physical Point("anode", 2) = {3};
Physical Line("n_region", 1) = {1};
Physical Line("p_region", 2) = {2};

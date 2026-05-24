class Dispute {
  final String id;
  final String candidateA; // Existing Marker ID
  final String candidateB; // New Contribution ID
  final String type;
  final Map<String, dynamic> votes;
  final String status;
  final Map<String, dynamic>? dataA; // Existing Marker Data
  final Map<String, dynamic>? dataB; // New Contribution Data
  final String? regionCode;
  final String? regionName;
  final String? cityCode;
  final String? cityName;

  Dispute({
    required this.id,
    required this.candidateA,
    required this.candidateB,
    required this.type,
    required this.votes,
    required this.status,
    this.dataA,
    this.dataB,
    this.regionCode,
    this.regionName,
    this.cityCode,
    this.cityName,
  });

  factory Dispute.fromJson(Map<String, dynamic> json) {
    return Dispute(
      id: (json['id'] ?? '').toString(),
      candidateA: (json['candidateA'] ?? json['target_id'] ?? '').toString(),
      candidateB: (json['candidateB'] ?? '').toString(),
      type: (json['type'] ?? json['target_type'] ?? 'conflict').toString(),
      votes: json['votes'] ?? {},
      status: (json['status'] ?? 'pending').toString(),
      dataA: json['dataA'],
      dataB: json['dataB'],
      regionCode: json['regionCode']?.toString(),
      regionName: json['regionName']?.toString(),
      cityCode: json['cityCode']?.toString(),
      cityName: json['cityName']?.toString(),
    );
  }
}

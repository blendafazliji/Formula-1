db.races.aggregate([
  { $unwind: "$results" },

  {
    $match: {
      "results.position": 1
    }
  },

  {
    $group: {
      _id: {
        driverId: "$results.driverid",
        driverName: "$results.driverName"
      },
      wins: { $sum: 1 }
    }
  },

  { $sort: { wins: -1 } },

  { $limit: 10 },

  {
    $project: {
      _id: 0,
      driverId: "$_id.driverId",
      driverName: "$_id.driverName",
      wins: 1
    }
  }
])

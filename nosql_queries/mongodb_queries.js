# Top drivers by wins
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

# Top constructors   write a sql and a mongodb query
db.races.aggregate([
  { $unwind: "$results" },

  {
    $match: {
      "results.position": 1
    }
  },

  {
    $group: {
      _id: "$results.constructorName",
      wins: { $sum: 1 }
    }
  },

  { $sort: { wins: -1 } },

  { $limit: 10 },

  {
    $project: {
      _id: 0,
      constructor: "$_id",
      wins: 1
    }
  }
])

# Most races participated
db.races.aggregate([
  { $unwind: "$results" },

  {
    $group: {
      _id: {
        race: "$raceid",
        driver: "$results.driverid"
      }
    }
  },

  {
    $group: {
      _id: "$_id.driver",
      totalRaces: { $sum: 1 }
    }
  },

  { $sort: { totalRaces: -1 } }
])

# Circuit with most races
db.races.aggregate([
  {
    $group: {
      _id: "$circuit.circuitId",
      circuitName: { $first: "$circuit.name" },
      totalRaces: { $sum: 1 }
    }
  },

  { $sort: { totalRaces: -1 } },

  { $limit: 1 },

  {
    $project: {
      _id: 0,
      circuitId: "$_id",
      circuitName: 1,
      totalRaces: 1
    }
  }
])

# Average finishing position per driver
db.races.aggregate([
  { $unwind: "$results" },

  {
    $match: {
      "results.position": { $ne: null }
    }
  },

  {
    $group: {
      _id: {
        driverId: "$results.driverid",
        driverName: "$results.driverName"
      },
      avgPosition: { $avg: "$results.position" }
    }
  },

  { $sort: { avgPosition: 1 } },

  { $limit: 10 },

  {
    $project: {
      _id: 0,
      driverId: "$_id.driverId",
      driverName: "$_id.driverName",
      avgPosition: 1
    }
  }
])

# Total points per constructor 
db.races.aggregate([
  { $unwind: "$results" },

  {
    $group: {
      _id: {
        constructorId: "$results.constructorid",
        constructorName: "$results.constructorName"
      },
      totalPoints: { $sum: "$results.points" }
    }
  },

  { $sort: { totalPoints: -1 } },

  {
    $project: {
      _id: 0,
      constructorId: "$_id.constructorId",
      constructorName: "$_id.constructorName",
      totalPoints: 1
    }
  }
])



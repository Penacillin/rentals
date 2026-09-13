```bash
curl 'https://api-v6.streeteasy.com/' \
  --compressed \
  -X POST \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0' \
  -H 'Accept: application/json' \
  -H 'Accept-Language: en-US,en;q=0.9' \
  -H 'Accept-Encoding: gzip, deflate, br, zstd' \
  -H 'Referer: https://streeteasy.com/' \
  -H 'app-version: 1.0.0' \
  -H 'os: web' \
  -H 'Content-Type: application/json' \
  -H 'X-Forwarded-Proto: https' \
  -H 'apollographql-client-name: srp-frontend-service' \
  -H 'apollographql-client-version: version  ffc520651254e2d438efbcfbd8d0244ed62ff71d' \
  -H 'Origin: https://streeteasy.com' \
  -H 'Connection: keep-alive' \
-H 'Cookie: <redacted; browser profile supplies session>' \
  -H 'Sec-Fetch-Dest: empty' \
  -H 'Sec-Fetch-Mode: cors' \
  -H 'Sec-Fetch-Site: same-site' \
  -H 'Priority: u=4' \
  -H 'TE: trailers' \
  --data-raw $'{"query":"\\n  query GetListingRental($input: SearchRentalsInput\041) {\\n    searchRentals(input: $input) {\\n      search {\\n        criteria\\n      }\\n      totalCount\\n      edges {\\n        ... on OrganicRentalEdge {\\n          node {\\n            id\\n            areaName\\n            bedroomCount\\n            buildingType\\n            fullBathroomCount\\n            geoPoint {\\n              latitude\\n              longitude\\n            }\\n            halfBathroomCount\\n            isPremiumHdpEnabled\\n            leadMedia {\\n              photo {\\n                  key\\n              }\\n            }\\n            price\\n            totalMonthlyPrice\\n            sourceGroupLabel\\n            status\\n            street\\n            unit\\n            urlPath\\n            tier\\n          }\\n        }\\n        ... on FeaturedRentalEdge {\\n          node {\\n            id\\n            areaName\\n            bedroomCount\\n            buildingType\\n            fullBathroomCount\\n            geoPoint {\\n              latitude\\n              longitude\\n            }\\n            halfBathroomCount\\n            leadMedia {\\n              photo {\\n                  key\\n              }\\n            }\\n            price\\n            totalMonthlyPrice\\n            sourceGroupLabel\\n            status\\n            street\\n            unit\\n            urlPath\\n            tier\\n          }\\n        }\\n      }\\n    }\\n  }\\n","variables":{"input":{"filters":{"rentalStatus":"ACTIVE","areas":[122,130,133],"price":{"lowerBound":null,"upperBound":7000}},"page":1,"perPage":500,"sorting":{"attribute":"RECOMMENDED","direction":"DESCENDING"},"userSearchToken":"1f370468-de28-479b-be3b-a1bd3e2ab4e4","adStrategy":"NONE"}}}'
```